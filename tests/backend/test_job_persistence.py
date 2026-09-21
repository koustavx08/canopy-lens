"""What survives the process that accepted the job.

A restart is simulated the way it actually happens: a second `JobStore` opened
on the same file, with no memory of what the first one was holding. Nothing
here reaches into the first store's dictionary, because the point is what the
file says on its own.
"""

from __future__ import annotations

import pytest

from app.services import jobs as jobs_module
from app.services.analysis import analyze_image
from app.services.jobs import JobError, JobStore


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jobs.db"


@pytest.fixture(scope="module")
def result(tmp_path_factory, scene_array):
    """One real analysis, shared: the pipeline is the slow part, not the store."""
    from ..conftest import write_raster

    raster = write_raster(tmp_path_factory.mktemp("persist") / "scene.tif", scene_array)
    # Pinned to the engine that needs no weights and no network, as everywhere
    # else in the suite: this is a test about storage, not about detection.
    return analyze_image(raster, engine="classical")


def add(store: JobStore, name: str = "scene.tif"):
    return store.create(image_name=name, boundary_name=None, parameters={"engine": "classical"})


class TestSurvivingARestart:
    def test_a_completed_result_is_still_there(self, db, result):
        store = JobStore(path=db)
        job = add(store)
        store.mark_completed(job.id, result)

        restored = JobStore(path=db).get(job.id)

        assert restored is not None
        assert restored.status == "completed"
        assert restored.result is not None

    def test_the_measured_figures_come_back_unchanged(self, db, result):
        store = JobStore(path=db)
        job = add(store)
        store.mark_completed(job.id, result)

        restored = JobStore(path=db).get(job.id).result

        assert restored.summary.tree_count == result.summary.tree_count
        assert restored.summary.total_canopy_area_m2 == result.summary.total_canopy_area_m2
        assert [c.id for c in restored.crowns] == [c.id for c in result.crowns]

    def test_the_crown_geometry_survives_too(self, db, result):
        # The reason the result is pickled rather than flattened to JSON: the
        # exports are rendered from these polygons on request, so a result that
        # came back without them would answer a download with an empty file.
        store = JobStore(path=db)
        job = add(store)
        store.mark_completed(job.id, result)

        restored = JobStore(path=db).get(job.id).result

        assert restored.crowns[0].geometry_wgs84.equals(result.crowns[0].geometry_wgs84)
        assert restored.crowns[0].area_m2 == result.crowns[0].area_m2

    def test_a_failure_keeps_its_own_code_and_status(self, db):
        store = JobStore(path=db)
        job = add(store)
        store.mark_failed(job.id, JobError(code="invalid_raster", message="unreadable", status_code=422))

        restored = JobStore(path=db).get(job.id)

        assert restored.status == "failed"
        assert restored.error.code == "invalid_raster"
        assert restored.error.status_code == 422

    def test_an_id_from_no_run_at_all_is_still_absent(self, db):
        assert JobStore(path=db).get("nosuchjob") is None


class TestInterruptedWork:
    def test_a_job_that_was_running_is_not_left_running_forever(self, db):
        store = JobStore(path=db)
        job = add(store)
        store.mark_running(job.id)

        restored = JobStore(path=db).get(job.id)

        assert restored.status == "failed"
        assert restored.error.code == "interrupted"

    def test_a_job_that_was_still_queued_is_closed_out_as_well(self, db):
        job = add(JobStore(path=db))

        restored = JobStore(path=db).get(job.id)

        assert restored.status == "failed"
        assert restored.error.code == "interrupted"

    def test_a_finished_job_is_not_disturbed_by_the_restart(self, db, result):
        store = JobStore(path=db)
        job = add(store)
        store.mark_completed(job.id, result)

        assert JobStore(path=db).get(job.id).status == "completed"


class TestSharedBetweenWorkers:
    def test_the_queue_is_counted_across_every_store_on_the_file(self, db):
        # Two workers sharing one file are held to one queue limit between
        # them. Counted per process, each would accept the whole limit itself.
        # Both opened before either accepts anything, which is how workers
        # start: closing out the last run's jobs happens at boot, not partway
        # through this one.
        one = JobStore(path=db)
        two = JobStore(path=db)

        for _ in range(3):
            add(one)

        assert two.count_queued() == 3

    def test_progress_written_by_one_worker_is_readable_by_another(self, db):
        writer = JobStore(path=db)
        reader = JobStore(path=db)
        job = add(writer)
        writer.mark_running(job.id)
        writer.update_progress(job.id, "detecting", 0.5)

        seen = reader.get(job.id)

        assert seen.stage == "detecting"
        assert seen.progress == pytest.approx(0.5)


class TestWhatIsNotReadBack:
    def test_a_result_from_another_version_is_not_handed_over_as_if_it_were_current(
        self, db, result, monkeypatch
    ):
        store = JobStore(path=db)
        job = add(store)
        store.mark_completed(job.id, result)

        monkeypatch.setattr(jobs_module, "__version__", "99.0.0")
        restored = JobStore(path=db).get(job.id)

        # Not "completed with nothing in it": a client told the analysis is
        # done will ask for the result, and must be told why there is none.
        assert restored.status == "failed"
        assert restored.error.code == "result_unreadable"

    def test_nowhere_to_write_is_not_a_reason_to_refuse_work(self, tmp_path):
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("this is a file", encoding="utf-8")
        store = JobStore(path=blocked / "jobs.db")

        job = add(store)

        # Degraded to memory alone, and said so in the log, rather than
        # failing to start over something that costs durability and no more.
        assert store.get(job.id).status == "queued"
        assert JobStore(path=blocked / "jobs.db").get(job.id) is None


class TestTheFileStaysBounded:
    def test_finished_rows_are_pruned_oldest_first(self, db):
        store = JobStore(max_jobs=3, path=db)
        finished = [add(store, f"old{i}.tif") for i in range(3)]
        for job in finished:
            store.mark_failed(job.id, JobError(code="x", message="y"))

        newcomer = add(store)

        assert store.get(finished[0].id) is None
        assert store.get(finished[2].id) is not None
        assert store.get(newcomer.id) is not None

    def test_clearing_empties_the_file_and_not_just_the_memory(self, db):
        store = JobStore(path=db)
        job = add(store)

        store.clear()

        assert JobStore(path=db).get(job.id) is None
