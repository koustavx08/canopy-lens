"""An in-memory register of analysis jobs.

An analysis takes long enough that holding the HTTP connection open for it is a
bad bargain: the browser gets no progress, a proxy may time out in the middle,
and a dropped connection loses work that had nearly finished. So `POST
/api/analyze` accepts the files, hands back a job id, and the client polls.

The store is a dictionary with a lock, and that is a deliberate choice rather
than a shortcut waiting to be upgraded. Results are useful for as long as the
tab that requested them is open; persisting them to a database would mean
inventing retention rules, migrations and cleanup for data whose whole lifetime
is a few minutes. What it does cost is stated plainly: **a restart forgets every
job**, and jobs do not survive across multiple worker processes. Both are
recorded in the README rather than discovered.

`AnalysisResult` is kept in memory as the object the pipeline produced, and CSV
and GeoJSON are rendered from it on request. Writing three files per job to disk
would make the API responsible for cleaning them up later, and the export
functions are fast enough that there is nothing to gain by it.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from .analysis import AnalysisResult

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "completed", "failed"]

MAX_JOBS = 50
"""How many jobs to remember.

Bounded because the store lives in the process: an unbounded dictionary of
analysis results is a memory leak with a polite name. Only finished jobs are
evicted, oldest first, so a burst of work can never discard something still
running.
"""


@dataclass
class JobError:
    """A failure expressed in the same shape as every other API error."""

    code: str
    message: str
    detail: dict = field(default_factory=dict)
    status_code: int = 400
    """The HTTP status the failure would have had if it had happened inline.

    Kept so that asking for the result of a failed job answers with the same
    code and status a synchronous call would have returned, rather than
    flattening every pipeline error into one generic 400.
    """


@dataclass
class Job:
    """One analysis, from accepted upload to available result."""

    id: str
    image_name: str
    boundary_name: str | None
    parameters: dict
    status: JobStatus = "queued"
    stage: str = "queued"
    progress: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: AnalysisResult | None = None
    error: JobError | None = None

    @property
    def processing_time_seconds(self) -> float | None:
        """Wall-clock time from the start of work to its end.

        Measured from when the analysis actually began, not from when the job
        was created: time spent queued behind other work is not time spent
        analysing this image, and reporting it as such would overstate how long
        the pipeline takes.
        """
        if self.started_at is None or self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    @property
    def is_finished(self) -> bool:
        return self.status in ("completed", "failed")


class JobStore:
    """A thread-safe, bounded register of jobs.

    Analyses run in a worker thread while requests are served on the event
    loop, so every read and write goes through one lock. The objects handed out
    by `get` are live: callers read them, and only the store mutates them.
    """

    def __init__(self, max_jobs: int = MAX_JOBS) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def create(self, *, image_name: str, boundary_name: str | None, parameters: dict) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            image_name=image_name,
            boundary_name=boundary_name,
            parameters=parameters,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._evict_locked()
        log.info("Created job %s for %s", job.id, image_name)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.stage = "starting"
            job.started_at = datetime.now(timezone.utc)

    def update_progress(self, job_id: str, stage: str, progress: float) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_finished:
                return
            job.stage = stage
            job.progress = progress

    def mark_completed(self, job_id: str, result: AnalysisResult) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "completed"
            job.stage = "completed"
            job.progress = 1.0
            job.result = result
            job.finished_at = datetime.now(timezone.utc)
        log.info("Job %s completed: %d crown(s)", job_id, len(result.crowns))

    def mark_failed(self, job_id: str, error: JobError) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.stage = "failed"
            job.error = error
            job.finished_at = datetime.now(timezone.utc)
        log.info("Job %s failed: %s - %s", job_id, error.code, error.message)

    def clear(self) -> None:
        """Forget every job. Used by tests to keep one case out of another's way."""
        with self._lock:
            self._jobs.clear()

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs until the store is back within budget."""
        while len(self._jobs) > self._max_jobs:
            victim = next((j for j in self._jobs.values() if j.is_finished), None)
            if victim is None:
                # Everything still running. Going over budget is the lesser
                # problem; discarding work in flight is the greater one.
                log.warning("Job store over budget with no finished job to evict")
                return
            del self._jobs[victim.id]
            log.info("Evicted job %s to stay within %d jobs", victim.id, self._max_jobs)


_store = JobStore()


def get_job_store() -> JobStore:
    """The process-wide job store, as a FastAPI dependency."""
    return _store
