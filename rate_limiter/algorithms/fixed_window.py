import math
import time
from typing import Callable, Tuple

import redis


def is_allowed(
    client: redis.Redis,
    key: str,
    limit: int,
    window_seconds: int,
    now_fn: Callable[[], float] = time.time,
) -> Tuple[bool, int, float]:
    """Fixed Window Counter rate limiter backed by Redis.

    Returns (allowed, current_count, reset_seconds) so callers can report
    remaining quota and reset time (e.g. X-RateLimit-Remaining/-Reset headers).
    """
    # Bucket id changes every `window_seconds`, so encoding it into the key
    # gives each window its own counter "for free" -- no manual reset logic,
    # old windows just become unreferenced keys that expire on their own.
    now = now_fn()
    window_id = int(now // window_seconds)
    redis_key = f"ratelimit:fixed:{key}:{window_id}"

    # INCR is atomic on the Redis server, so concurrent requests from the
    # same client can't race each other into under-counting (unlike a
    # GET-then-SET counter, which is a classic read-modify-write bug).
    count = client.incr(redis_key)

    # Only the request that creates the key (count == 1) sets the TTL.
    # Setting it unconditionally on every call would keep sliding the
    # expiry forward and the window would never actually roll over.
    if count == 1:
        # EXPIRE requires an integer number of seconds; window_seconds may
        # arrive as a float (e.g. from a query-string parameter parsed as
        # float), so this must be rounded up rather than passed through raw.
        client.expire(redis_key, math.ceil(window_seconds))

    allowed = count <= limit
    reset_seconds = (window_id + 1) * window_seconds - now
    return allowed, count, reset_seconds
