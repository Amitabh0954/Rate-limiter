# Dev Log & Technical Notes

This is the detailed companion doc to the top-level [README](../README.md) —
project layout, full setup/testing/API reference, stage-by-stage progress,
and every bug or design decision worth remembering later. The main README
stays short; this file is where the details live and get updated as work
continues.

> **Status**: Stages 1-5 complete (setup, all 3 algorithms with unit tests,
> FastAPI endpoint with integration tests). Stage 6 (load testing) is next.
> See [Progress](#progress) below.

---

## What this project actually does

This is **not** a protected API itself — it's a decision service. A real
gateway/proxy/backend would call `POST /check` before doing expensive work
for a given client, and reject the caller with `429` if the answer is "no."

```
client request
     │
     ▼
[ your gateway / backend ] ──POST /check?client_id=X&algo=Y──▶ [ this service ]
     │                                                                │
     │◀────────────────── 200 allowed / 429 blocked ─────────────────┘
     ▼
forward request to real backend (only if allowed)
```

Internally, each algorithm is a **pure function** — `is_allowed(redis_client,
key, ...) -> (bool, info)` — with no HTTP knowledge at all. The FastAPI layer
only parses the request, calls the right algorithm, and translates the
result into an HTTP status code. This separation is what makes each
algorithm independently unit-testable (see `tests/test_fixed_window.py`,
etc.) without spinning up a server.

## The three algorithms

| Algorithm | Redis structure | Answers | Memory per client | Known weakness |
|---|---|---|---|---|
| **Fixed Window Counter** | one integer counter, keyed by client + epoch-aligned time bucket | "How many requests in *this* wall-clock-aligned window?" | O(1) | Boundary burst: up to 2x the limit can slip through across a window edge |
| **Sliding Window Log** | sorted set (ZSET) of request timestamps per client | "How many requests in the last N seconds, counted from *right now*?" | O(limit) — one entry per request in the window | Memory scales with the limit; more Redis work per check |
| **Token Bucket** | hash of `{tokens, last_refill}` per client | "Does this client currently have budget?" (refills continuously, burst up to a cap) | O(1) | Slightly more complex reasoning (two tunable knobs: capacity + rate) |

A full comparison table (memory usage, burst handling, accuracy trade-offs)
will be written up formally in Stage 7.

### Why atomicity mattered for each one

- **Fixed Window**: a single Redis `INCR` is already atomic — no extra work needed.
- **Sliding Window Log** and **Token Bucket**: each needs multiple dependent
  Redis operations (evict-then-count-then-add; read-then-compute-then-write)
  to happen as one atomic unit, or concurrent requests can race each other
  into incorrect results. Both are implemented as **Lua scripts**, which
  Redis runs to completion with nothing else interleaved.

## Project layout

```
rate_limiter/
  algorithms/
    fixed_window.py        # is_allowed(client, key, limit, window_seconds, now_fn=time.time)
    sliding_window_log.py  # is_allowed(client, key, limit, window_seconds, now_fn=time.time)
    token_bucket.py        # is_allowed(client, key, capacity, refill_rate, now_fn=time.time)
  dependencies.py           # get_redis() -- FastAPI dependency, shared connection pool
  main.py                   # FastAPI app: GET /health, POST /check
tests/
  conftest.py               # shared `r` fixture (test Redis db=15) + make_clock() fake-clock helper
  test_fixed_window.py
  test_sliding_window_log.py
  test_token_bucket.py
  test_api.py               # end-to-end tests via FastAPI TestClient
docker-compose.yml          # `docker compose up -d` starts Redis on localhost:6379
requirements.txt
```

Every algorithm module accepts an injectable `now_fn` (defaults to
`time.time`), which is what lets tests simulate window rollover / token
refill deterministically and instantly, instead of using real `time.sleep()`
and risking flaky, slow tests.

## Setup

```bash
python -m venv venv
source venv/Scripts/activate        # Windows Git Bash; use venv\Scripts\Activate.ps1 in PowerShell
python -m pip install --upgrade pip
pip install -r requirements.txt

docker compose up -d                # starts Redis on localhost:6379 (requires Docker Desktop running)
```

## Running the tests

```bash
source venv/Scripts/activate
python -m pytest tests/ -v
```

All algorithm and API tests run against a **real Redis instance** on a
dedicated test DB (`db=15`, flushed before/after each test) rather than a
mock — a rate limiter's entire job is correctness under Redis's actual
atomicity guarantees, which a mock would paper over.

## Running the API

```bash
uvicorn rate_limiter.main:app --reload --port 8000
```

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

### `POST /check`

Checks whether `client_id` is allowed one more request under `algo`.
Returns `200` (allowed) or `429` (blocked).

| Param | Applies to | Default | Meaning |
|---|---|---|---|
| `client_id` | all | *(required)* | Identifies the caller being rate-limited |
| `algo` | all | `token_bucket` | `fixed_window` \| `sliding_window_log` \| `token_bucket` |
| `limit` | fixed_window, sliding_window_log | `5` | Max requests per window |
| `window_seconds` | fixed_window, sliding_window_log | `10` | Window size in seconds |
| `capacity` | token_bucket | `5` | Max burst size (bucket size) |
| `refill_rate` | token_bucket | `0.5` | Tokens added per second (sustained rate) |

Defaults are deliberately small so you can observe blocking within a few
seconds of manual testing — they are not meant to look like production
values.

```bash
# Fire 6 requests at a limit of 5 -- the 6th should come back 429
for i in 1 2 3 4 5 6; do
  curl -s -o /tmp/resp.json -w "%{http_code}\n" -X POST \
    "http://localhost:8000/check?client_id=demo&algo=fixed_window&limit=5&window_seconds=10"
  cat /tmp/resp.json; echo
done
```

Each `algo` namespaces its own Redis keys
(`ratelimit:fixed:`, `ratelimit:slidinglog:`, `ratelimit:tokenbucket:`), so
the same `client_id` can be checked against multiple algorithms
independently without interference.

## Progress

| Stage | What | Status |
|---|---|---|
| 1 | Project setup: venv, requirements, Redis via Docker | ✅ Done |
| 2 | Fixed Window Counter + unit tests | ✅ Done |
| 3 | Sliding Window Log + unit tests | ✅ Done |
| 4 | Token Bucket + unit tests | ✅ Done |
| 5 | FastAPI `POST /check` endpoint + integration tests | ✅ Done |
| 6 | Load-testing script proving the limiter actually blocks | ⬜ Next |
| 7 | README comparison table + architecture diagram writeup | ⬜ Pending |
| 8 | Dockerize the service (optional) | ⬜ Pending |

### Known gaps / things called out along the way

- `fixed_window.py` originally passed a `float` `window_seconds` straight to
  Redis `EXPIRE` (which requires an integer) — worked in unit tests (which
  always used `int`), broke once the API passed floats from query params.
  Fixed with `math.ceil()`. A good example of a bug that only surfaces at
  integration time, not unit-test time.
- No input validation yet on `limit`/`capacity`/`refill_rate` being positive
  (e.g. `limit=-5` isn't rejected). Not a correctness bug in what's been
  tested, but a known gap.
- Token Bucket's Redis key TTL (`capacity / refill_rate + 1` seconds) is a
  cleanup heuristic, not a strict correctness guarantee.
