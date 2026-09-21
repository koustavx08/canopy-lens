"""Request ids, readiness and the metrics endpoint.

The counters are process-wide and every other module in the suite adds to
them, so nothing here asserts an absolute total. What is asserted is that a
number went up, which is the only thing a counter promises.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.observability import METRICS

client = TestClient(create_app())


@pytest.fixture
def configured(monkeypatch):
    def build(**env) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(f"CANOPYLENS_{key.upper()}", str(value))
        get_settings.cache_clear()
        return TestClient(create_app())

    yield build
    get_settings.cache_clear()


class TestRequestIds:
    def test_every_response_carries_one(self):
        assert client.get("/api/health").headers["X-Request-ID"]

    def test_an_id_from_in_front_is_kept_rather_than_replaced(self):
        # A proxy that already assigned one is where the two halves of a trace
        # are joined; generating a second would break the join.
        response = client.get("/api/health", headers={"X-Request-ID": "from-the-proxy"})

        assert response.headers["X-Request-ID"] == "from-the-proxy"

    def test_a_caller_cannot_make_the_id_arbitrarily_long(self):
        # It is echoed in a header and written to every log line the request
        # produces, and the caller chooses it.
        response = client.get("/api/health", headers={"X-Request-ID": "x" * 500})

        assert len(response.headers["X-Request-ID"]) == 64

    def test_two_requests_do_not_share_one(self):
        first = client.get("/api/health").headers["X-Request-ID"]
        second = client.get("/api/health").headers["X-Request-ID"]

        assert first != second

    def test_a_refused_request_is_given_one_too(self):
        # The failures are the ones somebody will come asking about.
        response = client.get("/api/results/nosuchjob")

        assert response.status_code == 404
        assert response.headers["X-Request-ID"]


class TestReadiness:
    def test_a_working_development_instance_is_ready(self, configured):
        client = configured(environment="development", api_keys="")
        response = client.get("/api/health/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    def test_an_open_production_instance_is_degraded_but_still_serving(self, configured):
        # Worth seeing, not worth taking out of rotation: it can still analyse
        # imagery, and a 503 here would turn a warning into an outage.
        client = configured(environment="production", api_keys="")
        response = client.get("/api/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        failed = [c for c in body["checks"] if not c["ok"]]
        assert [c["name"] for c in failed] == ["access_control"]
        assert failed[0]["critical"] is False

    def test_closing_the_door_makes_it_ready_again(self, configured):
        client = configured(environment="production", api_keys="a-real-key")
        response = client.get("/api/health/ready", headers={"X-API-Key": "a-real-key"})

        assert response.json()["status"] == "ready"

    def test_nowhere_to_write_is_not_ready_at_all(self, configured, tmp_path):
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("this is a file", encoding="utf-8")
        client = configured(data_dir=str(blocked / "data"))

        response = client.get("/api/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert any(c["name"] == "data_directory_writable" and not c["ok"] for c in body["checks"])

    def test_the_probe_names_what_it_checked(self, configured):
        client = configured(environment="development")
        names = {c["name"] for c in client.get("/api/health/ready").json()["checks"]}

        assert names == {
            "data_directory_writable",
            "detection_engine",
            "job_record",
            "access_control",
        }

    def test_readiness_stays_open_when_a_key_is_required(self, configured):
        # An orchestrator that has to be given a key to ask whether the service
        # is up is one more secret to distribute.
        client = configured(api_keys="letmein")

        assert client.get("/api/health/ready").status_code in (200, 503)


class TestMetrics:
    def test_it_answers_in_the_exposition_format(self):
        response = client.get("/api/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "# TYPE canopylens_requests_total counter" in response.text

    def test_requests_are_counted(self):
        before = _requests_total()
        client.get("/api/capabilities")

        assert _requests_total() > before

    def test_the_endpoint_label_is_the_route_and_not_the_url(self):
        # One series per job id would give the scraper a new time series for
        # every analysis, which is how a server takes down its own monitoring.
        client.get("/api/jobs/some-job-id-that-does-not-exist")
        body = client.get("/api/metrics").text

        assert 'endpoint="/api/jobs/{job_id}"' in body
        assert "some-job-id-that-does-not-exist" not in body

    def test_an_unmatched_path_is_labelled_rather_than_dropped(self):
        client.get("/api/no-such-endpoint")

        assert 'endpoint="unmatched"' in client.get("/api/metrics").text

    def test_the_analysis_counters_are_published(self):
        body = client.get("/api/metrics").text

        for outcome in ("started", "completed", "failed", "cancelled"):
            assert f'canopylens_analyses_total{{outcome="{outcome}"}}' in body

    def test_the_queue_depth_is_read_at_scrape_time(self):
        assert "canopylens_analyses_queued " in client.get("/api/metrics").text

    def test_the_numbers_are_not_public_when_a_key_is_required(self, configured):
        guarded = configured(api_keys="letmein")

        assert guarded.get("/api/metrics").status_code == 401
        assert guarded.get("/api/metrics", headers={"X-API-Key": "letmein"}).status_code == 200


def _requests_total() -> int:
    """Every request the process has counted, across all labels."""
    total = 0
    for line in METRICS.render(queued=0).splitlines():
        if line.startswith("canopylens_requests_total{"):
            total += int(line.rsplit(" ", 1)[1])
    return total
