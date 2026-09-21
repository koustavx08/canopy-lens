"""Who may call the API, and how often.

Every case here builds its own app, because both limits are read once when the
application is constructed. A test that changed the setting afterwards would
be asserting against the configuration the previous test left behind.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.jobs import get_job_store


@pytest.fixture
def configured(monkeypatch):
    """Build a client for an app configured by environment, as deployment is."""

    def build(**env) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(f"CANOPYLENS_{key.upper()}", str(value))
        get_settings.cache_clear()
        return TestClient(create_app())

    yield build
    # Leave the process-wide settings as they were found, or the next module
    # to build an app inherits this one's limits.
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _empty_job_store():
    get_job_store().clear()
    yield
    get_job_store().clear()


class TestApiKey:
    def test_an_open_server_needs_no_key(self, configured):
        client = configured(api_keys="")
        assert client.post("/api/boundary/preview").status_code != 401

    def test_a_guarded_endpoint_refuses_a_caller_without_a_key(self, configured):
        client = configured(api_keys="letmein")
        response = client.post("/api/boundary/preview")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_a_wrong_key_is_refused(self, configured):
        client = configured(api_keys="letmein")
        response = client.post("/api/boundary/preview", headers={"X-API-Key": "letmeout"})
        assert response.status_code == 401

    def test_the_header_is_accepted(self, configured):
        client = configured(api_keys="letmein")
        response = client.post("/api/boundary/preview", headers={"X-API-Key": "letmein"})
        # 422 for the missing file, which is the endpoint's own complaint and
        # therefore proof the request got past the guard.
        assert response.status_code == 422

    def test_a_bearer_token_is_accepted(self, configured):
        client = configured(api_keys="letmein")
        response = client.post(
            "/api/boundary/preview", headers={"Authorization": "Bearer letmein"}
        )
        assert response.status_code == 422

    def test_any_of_several_configured_keys_is_accepted(self, configured):
        client = configured(api_keys="first, second ,third")
        for key in ("first", "second", "third"):
            assert client.post("/api/boundary/preview", headers={"X-API-Key": key}).status_code

    def test_the_probes_stay_open(self, configured):
        client = configured(api_keys="letmein")
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/capabilities").status_code == 200


class TestRateLimit:
    def test_a_caller_may_spend_its_allowance(self, configured):
        client = configured(rate_limit_per_minute=3)
        for _ in range(3):
            assert client.get("/api/capabilities").status_code == 200

    def test_and_is_refused_past_it(self, configured):
        client = configured(rate_limit_per_minute=3)
        for _ in range(3):
            client.get("/api/capabilities")
        response = client.get("/api/capabilities")
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"

    def test_a_refusal_says_when_to_come_back(self, configured):
        client = configured(rate_limit_per_minute=3)
        for _ in range(4):
            response = client.get("/api/capabilities")
        assert int(response.headers["Retry-After"]) >= 1

    def test_zero_disables_the_limit(self, configured):
        client = configured(rate_limit_per_minute=0)
        for _ in range(30):
            assert client.get("/api/capabilities").status_code == 200

    def test_the_probes_are_never_rate_limited(self, configured):
        # An orchestrator polls on a schedule it will not slow down for a 429,
        # and a liveness check that fails because the service is busy gets the
        # service restarted in the middle of the work that made it busy.
        client = configured(rate_limit_per_minute=1)
        for _ in range(10):
            assert client.get("/api/health").status_code == 200


class TestWhoTheCallerIs:
    def test_a_forwarded_address_is_ignored_when_nothing_is_in_front(self, configured):
        # Any client can write this header. Believing it on a directly exposed
        # server would mean the limit is lifted by anyone who knows it exists.
        client = configured(rate_limit_per_minute=1, trusted_proxy_hops=0)
        assert client.get("/api/capabilities", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert client.get("/api/capabilities", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429

    def test_one_proxy_in_front_makes_its_entry_the_caller(self, configured):
        client = configured(rate_limit_per_minute=1, trusted_proxy_hops=1)
        for address in ("1.1.1.1", "2.2.2.2"):
            response = client.get("/api/capabilities", headers={"X-Forwarded-For": address})
            assert response.status_code == 200

    def test_an_address_the_caller_prepended_does_not_buy_a_fresh_allowance(self, configured):
        # nginx appends the address it saw, so the trusted entry is the last
        # one. Everything before it is whatever the caller chose to send, and
        # varying it must not look like a different caller.
        client = configured(rate_limit_per_minute=1, trusted_proxy_hops=1)
        first = client.get("/api/capabilities", headers={"X-Forwarded-For": "9.9.9.9, 3.3.3.3"})
        second = client.get("/api/capabilities", headers={"X-Forwarded-For": "8.8.8.8, 3.3.3.3"})
        assert first.status_code == 200
        assert second.status_code == 429


class TestQueueCap:
    def test_a_full_queue_is_refused_before_the_upload_is_read(self, configured):
        client = configured(max_queued_analyses=2, rate_limit_per_minute=0)
        store = get_job_store()
        for _ in range(2):
            store.create(image_name="waiting.tif", boundary_name=None, parameters={})

        response = client.post(
            "/api/analyze", files={"image": ("scene.tif", b"not read", "image/tiff")}
        )
        assert response.status_code == 429
        body = response.json()["error"]
        assert body["code"] == "queue_full"
        assert body["detail"] == {"queued": 2, "limit": 2}

    def test_room_in_the_queue_lets_a_submission_through_the_cap(self, configured):
        client = configured(max_queued_analyses=2, rate_limit_per_minute=0)
        get_job_store().create(image_name="waiting.tif", boundary_name=None, parameters={})

        response = client.post(
            "/api/analyze", files={"image": ("scene.txt", b"not imagery", "text/plain")}
        )
        # Refused for what it is rather than for arriving too late: the cap let
        # it past and the format check stopped it.
        assert response.status_code == 415
