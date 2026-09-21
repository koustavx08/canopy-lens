"""Stopping an analysis that has not finished.

Cancelling has three parts and they are tested where each of them lives: the
store decides what a cancelled job looks like, the pipeline decides where it is
safe to stop, and the endpoint decides what a caller is told. Driving a running
analysis to a checkpoint over HTTP and cancelling it there would be a test of
how fast this machine happens to be, so the running case is exercised directly
against the pipeline instead.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.analysis import AnalysisCancelled, analyze_image
from app.services.jobs import JobError, JobStore, get_job_store

from . import await_job
from ..conftest import SCENE_CIRCLES, SCENE_SIZE, draw_scene, write_raster

client = TestClient(create_app())


def add(store: JobStore, name: str = "scene.tif"):
    return store.create(image_name=name, boundary_name=None, parameters={"engine": "classical"})


class TestTheStore:
    def test_a_queued_job_is_finished_on_the_spot(self):
        # Nothing has picked it up, so there is nothing to wait for.
        store = JobStore()
        job = add(store)

        store.request_cancel(job.id)

        assert store.get(job.id).status == "cancelled"
        assert store.get(job.id).error.code == "cancelled"

    def test_a_running_job_is_asked_rather_than_stopped(self):
        # Asking is not the same as having stopped: the thread notices at its
        # next checkpoint, and until then "running" is the honest answer.
        store = JobStore()
        job = add(store)
        store.mark_running(job.id)

        store.request_cancel(job.id)

        assert store.get(job.id).status == "running"
        assert store.cancel_requested(job.id) is True

    def test_a_job_nobody_cancelled_is_not_asked_to_stop(self):
        store = JobStore()
        job = add(store)
        store.mark_running(job.id)

        assert store.cancel_requested(job.id) is False

    def test_a_finished_job_is_returned_unchanged(self):
        store = JobStore()
        job = add(store)
        store.mark_failed(job.id, JobError(code="invalid_raster", message="unreadable"))

        assert store.request_cancel(job.id).status == "failed"

    def test_an_unknown_job_cannot_be_cancelled(self):
        assert JobStore().request_cancel("nosuchjob") is None

    def test_a_job_cancelled_in_the_queue_never_starts(self):
        # The pool may pick the job up after the cancel arrived. mark_running
        # is what tells the worker whether there is still work to do.
        store = JobStore()
        job = add(store)
        store.request_cancel(job.id)

        assert store.mark_running(job.id) is False
        assert store.get(job.id).status == "cancelled"

    def test_a_request_from_another_worker_is_seen_by_the_one_running_it(self, tmp_path):
        # The cancel can land on any worker. The one doing the work reads the
        # flag from the file it shares with the others.
        db = tmp_path / "jobs.db"
        owner = JobStore(path=db)
        other = JobStore(path=db)
        job = add(owner)
        owner.mark_running(job.id)

        other.request_cancel(job.id)

        assert owner.cancel_requested(job.id) is True


class TestThePipeline:
    @pytest.fixture(scope="class")
    def raster(self, tmp_path_factory):
        scene = draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)
        return write_raster(tmp_path_factory.mktemp("cancel") / "scene.tif", scene)

    def test_it_stops_when_asked(self, raster):
        with pytest.raises(AnalysisCancelled):
            analyze_image(raster, engine="classical", should_stop=lambda: True)

    def test_it_carries_on_when_not(self, raster):
        result = analyze_image(raster, engine="classical", should_stop=lambda: False)

        assert result.summary.tree_count == len(SCENE_CIRCLES)

    def test_a_check_that_cannot_answer_does_not_throw_away_the_work(self, raster):
        # Being wrong in this direction costs a wasted analysis. Being wrong in
        # the other costs one that had nearly finished.
        def broken() -> bool:
            raise RuntimeError("the cancel check caught fire")

        result = analyze_image(raster, engine="classical", should_stop=broken)

        assert result.summary.tree_count == len(SCENE_CIRCLES)


class TestTheEndpoint:
    @pytest.fixture(autouse=True)
    def _empty_store(self):
        get_job_store().clear()
        yield
        get_job_store().clear()

    @pytest.fixture(scope="class")
    def raster(self, tmp_path_factory):
        scene = draw_scene(SCENE_SIZE, SCENE_SIZE, SCENE_CIRCLES)
        return write_raster(tmp_path_factory.mktemp("cancel_api") / "scene.tif", scene)

    def test_cancelling_an_unknown_job_is_a_404(self):
        response = client.post("/api/jobs/nosuchjob/cancel")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_a_queued_job_is_reported_cancelled(self):
        job = get_job_store().create(
            image_name="waiting.tif", boundary_name=None, parameters={}
        )

        response = client.post(f"/api/jobs/{job.id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_asking_twice_is_not_an_error(self):
        job = get_job_store().create(
            image_name="waiting.tif", boundary_name=None, parameters={}
        )
        client.post(f"/api/jobs/{job.id}/cancel")

        assert client.post(f"/api/jobs/{job.id}/cancel").status_code == 200

    def test_a_finished_analysis_cannot_be_cancelled(self, raster):
        with open(raster, "rb") as handle:
            accepted = client.post(
                "/api/analyze",
                files={"image": ("scene.tif", handle, "image/tiff")},
                data={"engine": "classical"},
            )
        job_id = accepted.json()["job_id"]
        await_job(client, job_id)

        response = client.post(f"/api/jobs/{job_id}/cancel")

        # Answering "cancelled" here would misdescribe a result that exists.
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "job_not_finished"

    def test_the_result_of_a_cancelled_analysis_says_why_there_is_none(self):
        job = get_job_store().create(
            image_name="waiting.tif", boundary_name=None, parameters={}
        )
        client.post(f"/api/jobs/{job.id}/cancel")

        response = client.get(f"/api/results/{job.id}")

        # Not "not finished", which would be true of one still running.
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "cancelled"
