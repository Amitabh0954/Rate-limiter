from rate_limiter.algorithms.fixed_window import is_allowed
from tests.conftest import make_clock


def test_allows_requests_under_limit(r):
    now, _ = make_clock(0)
    for i in range(5):
        allowed, count, _ = is_allowed(r, "clientA", limit=5, window_seconds=60, now_fn=now)
        assert allowed is True
        assert count == i + 1


def test_blocks_requests_once_limit_exceeded(r):
    now, _ = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientB", limit=5, window_seconds=60, now_fn=now)

    allowed, count, _ = is_allowed(r, "clientB", limit=5, window_seconds=60, now_fn=now)

    assert allowed is False
    assert count == 6


def test_clients_have_independent_counters(r):
    now, _ = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientC", limit=5, window_seconds=60, now_fn=now)

    # A different client should be unaffected by clientC's usage.
    allowed, count, _ = is_allowed(r, "clientD", limit=5, window_seconds=60, now_fn=now)

    assert allowed is True
    assert count == 1


def test_counter_resets_when_window_rolls_over(r):
    now, advance = make_clock(0)
    for _ in range(3):
        is_allowed(r, "clientE", limit=3, window_seconds=60, now_fn=now)

    allowed, _, _ = is_allowed(r, "clientE", limit=3, window_seconds=60, now_fn=now)
    assert allowed is False  # 4th request in the same window is blocked

    advance(60)  # jump into the next window
    allowed, count, _ = is_allowed(r, "clientE", limit=3, window_seconds=60, now_fn=now)

    assert allowed is True
    assert count == 1  # counter started fresh in the new window


def test_boundary_burst_is_allowed_by_design(r):
    """Documents the known weakness of fixed windows: a client can send
    `limit` requests at the tail of one window and `limit` more at the
    head of the next, i.e. 2x the limit in a very short real time span.
    This isn't a bug -- it's the accuracy/memory trade-off we'll compare
    against Sliding Window Log in the README.
    """
    now, advance = make_clock(59)  # 1 second before window boundary (window=60s)
    for _ in range(5):
        is_allowed(r, "clientF", limit=5, window_seconds=60, now_fn=now)

    advance(1)  # now at t=60, a new window
    allowed_count = 0
    for _ in range(5):
        allowed, _, _ = is_allowed(r, "clientF", limit=5, window_seconds=60, now_fn=now)
        if allowed:
            allowed_count += 1

    # 10 requests allowed within a 2-second span, despite a limit of 5/min.
    assert allowed_count == 5


def test_reset_seconds_counts_down_to_window_boundary(r):
    now, advance = make_clock(10)  # 10s into a 60s window
    _, _, reset = is_allowed(r, "clientH", limit=5, window_seconds=60, now_fn=now)
    assert reset == 50  # 60 - 10

    advance(20)
    _, _, reset = is_allowed(r, "clientH", limit=5, window_seconds=60, now_fn=now)
    assert reset == 30  # 60 - 30


def test_ttl_is_set_so_old_windows_are_cleaned_up(r):
    now, _ = make_clock(1000)
    is_allowed(r, "clientG", limit=10, window_seconds=60, now_fn=now)

    keys = r.keys("ratelimit:fixed:clientG:*")
    assert len(keys) == 1

    ttl = r.ttl(keys[0])
    assert 0 < ttl <= 60
