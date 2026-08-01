from rate_limiter.algorithms.token_bucket import is_allowed
from tests.conftest import make_clock


def test_allows_burst_up_to_capacity(r):
    now, _ = make_clock(0)
    for i in range(5):
        allowed, tokens, _ = is_allowed(r, "clientA", capacity=5, refill_rate=1, now_fn=now)
        assert allowed is True
        assert tokens == 5 - (i + 1)


def test_blocks_once_bucket_is_empty(r):
    now, _ = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientB", capacity=5, refill_rate=1, now_fn=now)

    allowed, tokens, _ = is_allowed(r, "clientB", capacity=5, refill_rate=1, now_fn=now)

    assert allowed is False
    assert tokens == 0


def test_tokens_refill_over_time(r):
    now, advance = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientC", capacity=5, refill_rate=1, now_fn=now)  # drain bucket

    advance(3)  # 3 seconds idle at 1 token/sec -> 3 tokens available
    allowed, tokens, _ = is_allowed(r, "clientC", capacity=5, refill_rate=1, now_fn=now)

    assert allowed is True
    assert tokens == 2  # 3 refilled, minus 1 consumed by this request


def test_refill_is_capped_at_capacity(r):
    """Idling for a long time shouldn't let tokens accumulate past capacity
    -- otherwise a client could bank unlimited tokens and unleash an
    unbounded burst later, which defeats the whole point of a cap.
    """
    now, advance = make_clock(0)
    is_allowed(r, "clientD", capacity=5, refill_rate=1, now_fn=now)  # tokens: 5 -> 4

    advance(1000)  # way more than enough time to fully refill
    allowed, tokens, _ = is_allowed(r, "clientD", capacity=5, refill_rate=1, now_fn=now)

    assert allowed is True
    assert tokens == 4  # capacity(5) - 1 consumed just now, not 5 + 999


def test_clients_have_independent_buckets(r):
    now, _ = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientE", capacity=5, refill_rate=1, now_fn=now)

    allowed, tokens, _ = is_allowed(r, "clientF", capacity=5, refill_rate=1, now_fn=now)

    assert allowed is True
    assert tokens == 4


def test_reset_seconds_is_zero_when_tokens_remain(r):
    now, _ = make_clock(0)
    _, tokens, reset = is_allowed(r, "clientH", capacity=5, refill_rate=1, now_fn=now)
    assert tokens == 4
    assert reset == 0


def test_reset_seconds_counts_down_to_next_token_when_empty(r):
    now, _ = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientI", capacity=5, refill_rate=2, now_fn=now)  # drain bucket

    allowed, tokens, reset = is_allowed(r, "clientI", capacity=5, refill_rate=2, now_fn=now)
    assert allowed is False
    assert tokens == 0
    assert reset == 0.5  # (1 - 0) / 2 tokens-per-sec


def test_fractional_refill_across_small_time_slices(r):
    """Refill is proportional to elapsed time, not an all-or-nothing tick --
    a burst-tolerant limiter must still track partial tokens between
    requests, e.g. at 2 tokens/sec, 0.5s of idle time is worth 1 token.
    """
    now, advance = make_clock(0)
    for _ in range(10):
        is_allowed(r, "clientG", capacity=10, refill_rate=2, now_fn=now)  # drain fully

    advance(0.5)  # 0.5s * 2 tokens/sec = 1 token refilled
    allowed, tokens, _ = is_allowed(r, "clientG", capacity=10, refill_rate=2, now_fn=now)

    assert allowed is True
    assert tokens == 0  # 1 refilled, immediately consumed by this request
