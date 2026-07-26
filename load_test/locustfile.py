"""
Load test for the rate limiter API.

All simulated users deliberately hit the SAME client_id. A generic load
test asks "can the server handle traffic?" -- this one asks something more
specific: "does the admitted-request count ever exceed the configured
limit, even when many requests race each other to check it at once?" That's
the real test of whether the Redis-side atomicity (INCR / Lua scripts)
holds up under concurrency, which sequential pytest runs can't exercise.

Usage:
    locust -f load_test/locustfile.py --host=http://localhost:8000 \
        --headless --users 100 --spawn-rate 100 --run-time 5s \
        --algo fixed_window --limit 50 --window-seconds 60

`window_seconds` / `capacity` are chosen large relative to the run time so
the window/bucket doesn't roll over mid-test -- that keeps the "allowed
count must never exceed the limit" assertion clean and unambiguous.
"""

from locust import HttpUser, task, constant, events


@events.init_command_line_parser.add_listener
def _add_args(parser):
    parser.add_argument(
        "--algo", type=str, default="fixed_window",
        choices=["fixed_window", "sliding_window_log", "token_bucket"],
    )
    parser.add_argument("--client-id", type=str, default="loadtest-client")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--window-seconds", type=float, default=60)
    parser.add_argument("--capacity", type=float, default=50)
    parser.add_argument("--refill-rate", type=float, default=0.001)


class RateLimiterUser(HttpUser):
    # No think time -- the goal is to stress concurrency, not simulate
    # realistic user pacing.
    wait_time = constant(0)

    @task
    def check(self):
        opts = self.environment.parsed_options
        params = {"client_id": opts.client_id, "algo": opts.algo}
        if opts.algo == "token_bucket":
            params["capacity"] = opts.capacity
            params["refill_rate"] = opts.refill_rate
        else:
            params["limit"] = opts.limit
            params["window_seconds"] = opts.window_seconds

        # catch_response=True lets us override Locust's default "4xx is a
        # failure" behavior: a 429 here means the rate limiter did its job
        # correctly, not that something broke.
        with self.client.post(
            "/check", params=params, name=f"/check[{opts.algo}]", catch_response=True
        ) as response:
            if response.status_code in (200, 429):
                response.success()
            else:
                response.failure(f"unexpected status code {response.status_code}")


# Plain module-level dict, not thread-locked: Locust runs users as gevent
# greenlets cooperatively scheduled on a single OS thread (in the default,
# non---processes mode this script assumes), so increments here can't
# interleave the way they could across real OS threads.
_counts = {"allowed": 0, "blocked": 0, "other": 0}


@events.request.add_listener
def _tally(response, exception, **kwargs):
    if exception or response is None:
        _counts["other"] += 1
    elif response.status_code == 200:
        _counts["allowed"] += 1
    elif response.status_code == 429:
        _counts["blocked"] += 1
    else:
        _counts["other"] += 1


@events.test_stop.add_listener
def _summary(environment, **kwargs):
    opts = environment.parsed_options
    total = sum(_counts.values())
    expected_limit = opts.capacity if opts.algo == "token_bucket" else opts.limit

    print("\n" + "=" * 60)
    print("RATE LIMITER LOAD TEST SUMMARY")
    print("=" * 60)
    print(f"algo:            {opts.algo}")
    print(f"configured cap:  {expected_limit}")
    print(f"total requests:  {total}")
    print(f"allowed (200):   {_counts['allowed']}")
    print(f"blocked (429):   {_counts['blocked']}")
    print(f"unexpected:      {_counts['other']}")

    if _counts["allowed"] > expected_limit:
        print(
            f"FAIL: allowed count ({_counts['allowed']}) exceeded the "
            f"configured limit ({expected_limit}) -- the Redis-side "
            f"atomicity broke under concurrent load."
        )
    else:
        print(
            "PASS: allowed count never exceeded the configured limit, "
            "even under concurrent load from multiple simulated users."
        )
    print("=" * 60)
