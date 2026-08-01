from rate_limiter.algorithms.sliding_window_log import is_allowed
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
    assert count == 5  # log wasn't appended to since the request was denied


def test_clients_have_independent_logs(r):
    now, _ = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientC", limit=5, window_seconds=60, now_fn=now)

    allowed, count, _ = is_allowed(r, "clientD", limit=5, window_seconds=60, now_fn=now)

    assert allowed is True
    assert count == 1


def test_old_requests_slide_out_of_the_window(r):
    now, advance = make_clock(0)
    for _ in range(3):
        is_allowed(r, "clientE", limit=3, window_seconds=60, now_fn=now)

    allowed, _, _ = is_allowed(r, "clientE", limit=3, window_seconds=60, now_fn=now)
    assert allowed is False  # 4th request still within the 60s window

    # Move forward far enough that all 3 earlier requests have aged out,
    # but note: unlike Fixed Window, this isn't tied to a wall-clock
    # boundary -- it slides continuously with elapsed time.
    advance(60.001)
    allowed, count, _ = is_allowed(r, "clientE", limit=3, window_seconds=60, now_fn=now)

    assert allowed is True
    assert count == 1


def test_no_boundary_burst_unlike_fixed_window(r):
    """The core advantage over Fixed Window: sending `limit` requests right
    before a would-be boundary does NOT grant another `limit` requests
    immediately after -- the window is measured from *now*, continuously,
    not from an epoch-aligned bucket edge.
    """
    now, advance = make_clock(0)
    for _ in range(5):
        is_allowed(r, "clientF", limit=5, window_seconds=60, now_fn=now)

    advance(1)  # only 1 second later -- all 5 earlier requests are still in-window
    allowed, count, _ = is_allowed(r, "clientF", limit=5, window_seconds=60, now_fn=now)

    assert allowed is False
    assert count == 5


def test_reset_seconds_counts_down_to_oldest_entry_expiry(r):
    now, advance = make_clock(0)
    is_allowed(r, "clientH", limit=2, window_seconds=10, now_fn=now)  # t=0, oldest entry

    advance(4)  # t=4 -- the t=0 entry (score 0) is still the oldest, ages out at t=10
    _, _, reset = is_allowed(r, "clientH", limit=2, window_seconds=10, now_fn=now)
    assert reset == 6  # 0 + 10 - 4

    advance(6)  # t=10 -- the t=0 entry has just aged out, leaving only the t=4 one
    _, _, reset = is_allowed(r, "clientH", limit=2, window_seconds=10, now_fn=now)
    assert reset == 4  # 4 + 10 - 10


def test_reset_seconds_is_zero_for_the_very_first_request(r):
    now, _ = make_clock(0)
    _, _, reset = is_allowed(r, "clientI", limit=5, window_seconds=10, now_fn=now)
    assert reset == 0  # no prior entries to measure against yet


def test_partial_expiry_frees_exactly_one_slot(r):
    """Requests age out individually as the window slides, not all-or-nothing
    like a fixed window's bucket reset.
    """
    now, advance = make_clock(0)
    is_allowed(r, "clientG", limit=2, window_seconds=10, now_fn=now)  # t=0

    advance(5)
    is_allowed(r, "clientG", limit=2, window_seconds=10, now_fn=now)  # t=5, count=2, full

    advance(5.001)  # t=10.001 -- the t=0 request is now older than 10s and slides out
    allowed, count, _ = is_allowed(r, "clientG", limit=2, window_seconds=10, now_fn=now)

    assert allowed is True
    assert count == 2  # the t=5 request plus this new one; t=0 one evicted
