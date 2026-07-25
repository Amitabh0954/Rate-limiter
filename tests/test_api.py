import redis
from fastapi.testclient import TestClient

from rate_limiter.main import app
from rate_limiter.dependencies import get_redis
from tests.conftest import TEST_DB


def _override_get_redis():
    # Point the API at the same test DB the `r` fixture flushes, instead of
    # the production db=0 the app normally talks to -- otherwise every API
    # test run would leave real-looking rate-limit keys in db 0.
    return redis.Redis(host="localhost", port=6379, db=TEST_DB, decode_responses=True)


app.dependency_overrides[get_redis] = _override_get_redis

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_first_request_is_allowed_for_each_algo(r):
    for algo in ("fixed_window", "sliding_window_log", "token_bucket"):
        response = client.post("/check", params={"client_id": "clientA", "algo": algo})
        assert response.status_code == 200
        assert response.json()["allowed"] is True


def test_fixed_window_blocks_after_limit_via_api(r):
    for _ in range(5):
        response = client.post(
            "/check",
            params={"client_id": "clientB", "algo": "fixed_window", "limit": 5, "window_seconds": 10},
        )
        assert response.status_code == 200

    response = client.post(
        "/check",
        params={"client_id": "clientB", "algo": "fixed_window", "limit": 5, "window_seconds": 10},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["allowed"] is False


def test_sliding_window_log_blocks_after_limit_via_api(r):
    for _ in range(5):
        response = client.post(
            "/check",
            params={"client_id": "clientC", "algo": "sliding_window_log", "limit": 5, "window_seconds": 10},
        )
        assert response.status_code == 200

    response = client.post(
        "/check",
        params={"client_id": "clientC", "algo": "sliding_window_log", "limit": 5, "window_seconds": 10},
    )

    assert response.status_code == 429


def test_token_bucket_allows_burst_then_blocks_via_api(r):
    for _ in range(5):
        response = client.post(
            "/check",
            params={"client_id": "clientD", "algo": "token_bucket", "capacity": 5, "refill_rate": 0.01},
        )
        assert response.status_code == 200

    response = client.post(
        "/check",
        params={"client_id": "clientD", "algo": "token_bucket", "capacity": 5, "refill_rate": 0.01},
    )

    assert response.status_code == 429


def test_different_algos_have_independent_state_for_same_client(r):
    # Drain the fixed_window bucket for clientE...
    for _ in range(5):
        client.post(
            "/check",
            params={"client_id": "clientE", "algo": "fixed_window", "limit": 5, "window_seconds": 10},
        )

    # ...but sliding_window_log for the same client_id should be untouched,
    # since each algorithm namespaces its own Redis keys.
    response = client.post(
        "/check",
        params={"client_id": "clientE", "algo": "sliding_window_log", "limit": 5, "window_seconds": 10},
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True


def test_invalid_algo_is_rejected_with_422():
    response = client.post("/check", params={"client_id": "clientF", "algo": "not_a_real_algo"})
    assert response.status_code == 422


def test_missing_client_id_is_rejected_with_422():
    response = client.post("/check", params={"algo": "token_bucket"})
    assert response.status_code == 422
