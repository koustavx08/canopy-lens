"""The job store, and the progress it reports.

The HTTP tests cannot see a job midway through, because the test client runs
background work before returning. These tests drive the store directly, which
is where the intermediate states actually live.
"""

from __future__ import annotations

import pytest

from app.services.analysis import analyze_image
from app.services.jobs import JobError, JobStore

from ..conftest import write_raster


def make_store() -> JobStore:
    return JobStore()


def add(store: JobStore, name: str = "scene.tif"):
    return store.create(image_name=name, boundary_name=None, parameters={})


class TestLifecycle:
    def test_a_new_job_is_queued_and_has_produced_nothing(self):
        job = add(make_store())

        assert job.status == "queued"
        assert job.progress == 0.0
        assert job.result is None
        assert job.error is None

    def test_an_unknown_id_is_absent_rather_than_invented(self):
        assert make_store().get("nosuchjob") is None

    def test_progress_is_recorded_while_the_job_runs(self):
        store = make_store()
        job = add(store)

        store.mark_running(job.id)
        store.update_progress(job.id, "detecting", 0.42)

        assert store.get(job.id).status == "running"
        assert store.get(job.id).stage == "detecting"
        assert store.get(job.id).progress == 0.42

    def test_a_completed_job_is_finished_at_full_progress(self, utm_raster):
        store = make_store()
        job = add(store)
        result = analyze_image(utm_raster)

        store.mark_running(job.id)
        store.mark_completed(job.id, result)

        stored = store.get(job.id)
        assert stored.status == "completed"
        assert stored.progress == 1.0
        assert stored.result is result

    def test_a_failed_job_keeps_the_reason_it_failed(self):
        store = make_store()
        job = add(store)

        store.mark_failed(job.id, JobError(code="invalid_raster", message="unreadable"))

        stored = store.get(job.id)
        assert stored.status == "failed"
        assert stored.error.code == "invalid_raster"
        assert stored.is_finished

    def test_late_progress_cannot_reopen_a_finished_job(self):
        # A worker thread reporting after the job ended must not drag a
        # completed job back to 60%.
        store = make_store()
        job = add(store)
        store.mark_failed(job.id, JobError(code="x", message="y"))

        store.update_progress(job.id, "detecting", 0.6)

        assert store.get(job.id).status == "failed"
        assert store.get(job.id).progress == 0.0

    def test_updating_a_job_that_no_longer_exists_is_harmless(self):
        store = make_store()

        store.mark_running("gone")
        store.update_progress("gone", "detecting", 0.5)
        store.mark_failed("gone", JobError(code="x", message="y"))

        assert store.get("gone") is None


class TestProcessingTime:
    def test_it_is_unknown_until_the_work_has_ended(self):
        store = make_store()
        job = add(store)
        assert job.processing_time_seconds is None

        store.mark_running(job.id)
        assert store.get(job.id).processing_time_seconds is None

    def test_it_measures_work_not_waiting(self):
        # Timed from when the analysis started, not from when the job was
        # created: queue time is not time the pipeline spent on this image.
        from datetime import datetime, timedelta, timezone

        store = make_store()
        job = add(store)
        now = datetime.now(timezone.utc)
        job.created_at = now - timedelta(seconds=100)
        job.started_at = now - timedelta(seconds=2)
        job.finished_at = now

        assert job.processing_time_seconds == pytest.approx(2.0, abs=0.01)


class TestEviction:
    def test_finished_jobs_are_forgotten_oldest_first(self):
        store = JobStore(max_jobs=3)
        finished = [add(store, f"old{i}.tif") for i in range(3)]
        for job in finished:
            store.mark_failed(job.id, JobError(code="x", message="y"))

        newcomer = add(store)

        assert store.get(finished[0].id) is None
        assert store.get(finished[1].id) is not None
        assert store.get(newcomer.id) is not None

    def test_work_in_flight_is_never_discarded_to_make_room(self):
        # Going over budget is a smaller problem than throwing away an
        # analysis somebody is waiting for.
        store = JobStore(max_jobs=2)
        running = [add(store, f"busy{i}.tif") for i in range(2)]
        for job in running:
            store.mark_running(job.id)

        newcomer = add(store)

        assert all(store.get(job.id) is not None for job in running)
        assert store.get(newcomer.id) is not None


class TestProgressFromThePipeline:
    @pytest.fixture(scope="class")
    def trace(self, tmp_path_factory, scene_array) -> list[tuple[str, float]]:
        raster = write_raster(tmp_path_factory.mktemp("progress") / "scene.tif", scene_array)
        events: list[tuple[str, float]] = []
        analyze_image(
            raster,
            on_progress=lambda stage, fraction: events.append((stage, fraction)),
        )
        return events

    def test_the_pipeline_reports_the_stages_it_passes_through(self, trace):
        stages = [stage for stage, _ in trace]

        assert stages[0] == "reading"
        assert "detecting" in stages
        assert "measuring" in stages
        assert stages[-1] == "summarising"

    def test_progress_only_moves_forward(self, trace):
        fractions = [fraction for _, fraction in trace]

        assert fractions == sorted(fractions)
        assert 0.0 <= fractions[0] <= fractions[-1] <= 1.0

    def test_detection_progress_counts_tiles_rather_than_seconds(self, trace):
        # One report per tile, so the figure is a measure of work completed.
        detecting = [fraction for stage, fraction in trace if stage == "detecting"]

        assert len(detecting) >= 2
        assert len(set(detecting)) == len(detecting)

    def test_a_broken_reporter_cannot_fail_an_analysis(self, utm_raster):
        def explode(stage: str, fraction: float) -> None:
            raise RuntimeError("the progress bar caught fire")

        result = analyze_image(utm_raster, on_progress=explode)

        assert result.summary.tree_count > 0
