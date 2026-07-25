import redis

# One shared connection pool for the process. redis-py's ConnectionPool
# hands out/reclaims individual connections per call rather than opening a
# new TCP connection on every request, so a single pool -- not a fresh
# Redis client -- is what should be reused across requests.
_pool = redis.ConnectionPool(host="localhost", port=6379, db=0, decode_responses=True)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)
