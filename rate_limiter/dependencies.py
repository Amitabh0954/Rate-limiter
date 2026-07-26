import os

import redis

# Configurable via env var rather than hardcoded: when this app runs inside
# a Docker container, "localhost" would resolve to the container itself,
# not the separate Redis container -- Compose gives each service a DNS name
# (here, "redis") on the shared network, and REDIS_HOST is set to that name
# in docker-compose.yml. Running the app directly on the host (no Docker)
# still defaults to localhost with no extra setup needed.
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# One shared connection pool for the process. redis-py's ConnectionPool
# hands out/reclaims individual connections per call rather than opening a
# new TCP connection on every request, so a single pool -- not a fresh
# Redis client -- is what should be reused across requests.
_pool = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)
