# Rate Limiter as a Service

A rate limiter built from scratch in Python, FastAPI, and Redis. It implements three different rate-limiting algorithms — Fixed Window Counter, Sliding Window Log, and Token Bucket — behind a single HTTP endpoint, so they can be compared directly rather than just picking one.

This isn't a CRUD app. The interesting part is underneath: atomic counters under concurrent load, race conditions solved with Redis Lua scripts, and the accuracy/memory trade-offs that come with each algorithm choice.

## How it works

`POST /check` is a decision endpoint, not a protected resource. A gateway or backend service would call it before doing real work for a client, and back off with a `429` if told no.

```
client ──▶ your API/gateway ──POST /check?client_id=X&algo=Y──▶ rate limiter
                   ▲                                                  │
                   └──────────── 200 allowed / 429 blocked ───────────┘
```

Each algorithm is a plain Python function — `is_allowed(redis_client, key, ...)` — with no knowledge of HTTP. FastAPI just parses the request, calls the right one, and turns the result into a status code. That separation is what makes each algorithm independently unit-testable without a running server.

## The three algorithms

| Algorithm | How it tracks state | Trade-off |
|---|---|---|
| Fixed Window Counter | one counter per client per time bucket | cheapest, but allows up to 2x burst at window boundaries |
| Sliding Window Log | a Redis sorted set of request timestamps per client | accurate, but memory grows with the request limit |
| Token Bucket | a token count that refills continuously | tunable burst vs. sustained rate, the shape most real APIs use |

## Stack

Python · FastAPI · Redis · pytest · Locust · Docker

## Quickstart

Everything in Docker:

```bash
docker compose up -d --build       # builds the app image, starts Redis + the app together
curl http://localhost:8000/health
```

Or run the app directly on the host (needed for `--reload` / running tests), with just Redis in Docker:

```bash
python -m venv venv
source venv/Scripts/activate
pip install -r requirements-dev.txt

docker compose up -d redis
python -m pytest tests/ -v
uvicorn rate_limiter.main:app --reload --port 8000
```

Try it:

```bash
curl -X POST "http://localhost:8000/check?client_id=demo&algo=token_bucket"
```

Or open `http://localhost:8000/docs` for the interactive Swagger UI.

## Status

Complete: all three algorithms, the API, a Locust load test proving admitted requests never exceed the configured limit under concurrent load, and the service runs fully in Docker.

For full setup details, the API reference, project layout, the full algorithm comparison and architecture breakdown, and a running log of design decisions and bugs found along the way, see [docs/DEVLOG.md](docs/DEVLOG.md).
