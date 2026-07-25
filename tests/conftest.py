import redis
import pytest

# Dedicated Redis DB index for tests, so they never touch/clobber whatever
# data lives in db 0 during manual testing or the future API.
TEST_DB = 15


@pytest.fixture
def r():
    client = redis.Redis(host="localhost", port=6379, db=TEST_DB, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()


def make_clock(start: float):
    """A controllable fake clock, injected via `now_fn`.

    Using real time.sleep() to test window rollover would make tests slow
    and occasionally flaky (if a test happens to straddle a real window
    boundary by coincidence). Injecting the clock makes window rollover
    fully deterministic and instant to test.
    """
    state = {"t": start}

    def now():
        return state["t"]

    def advance(seconds):
        state["t"] += seconds

    return now, advance
