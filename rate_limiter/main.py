import math
from typing import Literal, Optional

import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Response

from rate_limiter.algorithms import fixed_window, sliding_window_log, token_bucket
from rate_limiter.dependencies import get_redis

app = FastAPI(title="Rate Limiter as a Service")

Algo = Literal["fixed_window", "sliding_window_log", "token_bucket"]

# Deliberately small/fast defaults so the limiter's behavior (allow, then
# block) is observable within a few seconds of manual testing or load
# testing, not tuned to look like a production API's actual limits.
DEFAULTS = {
    "fixed_window": {"limit": 5, "window_seconds": 10},
    "sliding_window_log": {"limit": 5, "window_seconds": 10},
    "token_bucket": {"capacity": 5, "refill_rate": 0.5},
}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check")
def check(
    response: Response,
    client_id: str,
    algo: Algo = "token_bucket",
    limit: Optional[int] = Query(default=None, gt=0),
    window_seconds: Optional[float] = Query(default=None, gt=0),
    capacity: Optional[float] = Query(default=None, gt=0),
    refill_rate: Optional[float] = Query(default=None, gt=0),
    redis_client: redis.Redis = Depends(get_redis),
):
    """Check whether `client_id` is allowed one more request under `algo`.

    Each algorithm namespaces its own Redis keys (see the `ratelimit:<algo>:`
    prefixes in each module), so the same client_id can be checked against
    multiple algorithms independently -- useful for the Stage 6 load test,
    which compares all three side by side.
    """
    if algo == "fixed_window" or algo == "sliding_window_log":
        defaults = DEFAULTS[algo]
        eff_limit = limit if limit is not None else defaults["limit"]
        eff_window = window_seconds if window_seconds is not None else defaults["window_seconds"]

        fn = fixed_window.is_allowed if algo == "fixed_window" else sliding_window_log.is_allowed
        allowed, count, reset_seconds = fn(redis_client, client_id, eff_limit, eff_window)

        rate_limit = eff_limit
        remaining = max(0, eff_limit - count)
        body = {
            "allowed": allowed,
            "algo": algo,
            "limit": eff_limit,
            "window_seconds": eff_window,
            "remaining": remaining,
        }
    else:
        defaults = DEFAULTS["token_bucket"]
        eff_capacity = capacity if capacity is not None else defaults["capacity"]
        eff_refill_rate = refill_rate if refill_rate is not None else defaults["refill_rate"]

        allowed, tokens, reset_seconds = token_bucket.is_allowed(
            redis_client, client_id, eff_capacity, eff_refill_rate
        )

        rate_limit = eff_capacity
        remaining = round(tokens, 3)
        body = {
            "allowed": allowed,
            "algo": algo,
            "capacity": eff_capacity,
            "refill_rate": eff_refill_rate,
            "remaining": remaining,
        }

    # Standard rate-limit headers so a real gateway/backend can act on this
    # decision without re-deriving it -- reset_seconds is always rounded up
    # so callers never retry a moment too early.
    reset_seconds_ceil = math.ceil(max(0.0, reset_seconds))
    headers = {
        "X-RateLimit-Limit": str(rate_limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset_seconds_ceil),
    }

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=body,
            headers={**headers, "Retry-After": str(reset_seconds_ceil)},
        )

    response.headers.update(headers)
    return body
