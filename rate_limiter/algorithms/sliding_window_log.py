import time
import uuid
from typing import Callable, Tuple

import redis

# All three steps (evict old entries, count survivors, add the new entry)
# must happen as one atomic unit -- otherwise two concurrent requests could
# both read the same "under limit" count before either has added its own
# entry, and both get admitted. Redis guarantees a Lua script runs to
# completion with no other command interleaved, which a Python-side
# pipeline of separate calls cannot.
_SLIDING_LOG_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)

-- Seconds until the oldest surviving entry ages out of the window, i.e.
-- the earliest a slot frees up. 0 if the log is currently empty.
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset = 0
if #oldest > 0 then
    reset = tonumber(oldest[2]) + window - now
end

if count < limit then
    redis.call('ZADD', key, now, member)
    -- Safety-net TTL: if this client goes silent, the whole log should
    -- eventually vanish rather than sit in Redis forever.
    redis.call('EXPIRE', key, math.ceil(window))
    return {1, count + 1, tostring(reset)}
else
    return {0, count, tostring(reset)}
end
"""


def is_allowed(
    client: redis.Redis,
    key: str,
    limit: int,
    window_seconds: float,
    now_fn: Callable[[], float] = time.time,
) -> Tuple[bool, int, float]:
    """Sliding Window Log rate limiter backed by a Redis sorted set.

    Returns (allowed, current_count, reset_seconds) -- reset_seconds is how
    long until the oldest entry currently in the window ages out.
    """
    now = now_fn()
    redis_key = f"ratelimit:slidinglog:{key}"

    # ZADD members must be unique within the set -- two requests landing on
    # the exact same timestamp (very plausible with a fake clock in tests,
    # and not impossible under real load) would otherwise collide and one
    # would silently overwrite the other, undercounting.
    member = f"{now}:{uuid.uuid4()}"

    allowed, count, reset_seconds = client.eval(
        _SLIDING_LOG_SCRIPT, 1, redis_key, now, window_seconds, limit, member
    )
    return bool(allowed), int(count), float(reset_seconds)
