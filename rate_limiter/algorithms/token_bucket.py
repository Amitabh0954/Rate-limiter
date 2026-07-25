import time
from typing import Callable, Tuple

import redis

# Refilling is computed lazily on each request (elapsed time * rate) instead
# of via a background ticker -- a ticker would need to run per-client,
# forever, and would need its own coordination story across multiple app
# instances. Lazy refill needs no scheduler at all: the bucket's state is
# just (tokens, last_refill_time), and "catching up" happens inline.
#
# Read-modify-write across two fields, so it must be atomic for the same
# reason Sliding Window Log needed a script: two concurrent requests must
# not both read the same starting token count.
_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_rate = tonumber(ARGV[3])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    -- First request from this client: bucket starts full.
    tokens = capacity
    last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)

-- Once idle long enough for the bucket to fully refill, there's nothing
-- left worth remembering -- let the key expire instead of keeping a stale
-- (but harmless) entry around forever.
local ttl = math.ceil(capacity / refill_rate) + 1
redis.call('EXPIRE', key, ttl)

-- Redis truncates Lua numbers to integers when returning them over RESP,
-- so tokens (a float) must be sent back as a string to preserve precision.
return {allowed, tostring(tokens)}
"""


def is_allowed(
    client: redis.Redis,
    key: str,
    capacity: float,
    refill_rate: float,
    now_fn: Callable[[], float] = time.time,
) -> Tuple[bool, float]:
    """Token Bucket rate limiter backed by a Redis hash.

    capacity: max tokens the bucket can hold (i.e. max burst size).
    refill_rate: tokens added per second (i.e. sustained allowed rate).

    Returns (allowed, tokens_remaining).
    """
    now = now_fn()
    redis_key = f"ratelimit:tokenbucket:{key}"

    allowed, tokens_remaining = client.eval(
        _TOKEN_BUCKET_SCRIPT, 1, redis_key, now, capacity, refill_rate
    )
    return bool(int(allowed)), float(tokens_remaining)
