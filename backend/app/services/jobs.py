"""A register of analysis jobs, in memory and on disk.

An analysis takes long enough that holding the HTTP connection open for it is a
bad bargain: the browser gets no progress, a proxy may time out in the middle,
and a dropped connection loses work that had nearly finished. So `POST
/api/analyze` accepts the files, hands back a job id, and the client polls.

Memory is the working copy and a SQLite file is the record. Every change is
written through to the file as it happens, so a restart does not lose a
finished result and a second worker process can answer a poll for a job it did
not run. Reads come from memory when the job is there, which is the common
case - the process that accepted a job is usually the one being asked about
it, and a progress poll should not touch the disk.

The two halves answer different questions. Memory holds the live object the
pipeline is writing into; the file holds what survives the process that wrote
it. A store built without a path keeps only the memory half, which is what the
unit tests use and what an instance with nowhere writable falls back to rather
than refusing to start.

`AnalysisResult` is stored as a pickle, keyed by the version of the application
that wrote it, and a row written by a different version is ignored rather than
loaded. A hand-written serialiser for a tree of dataclasses, shapely geometries
and CRS objects would be a second definition of those types, drifting from the
first one silently as they change; a pickle cannot drift, and the version key
removes the one case where it would be read by code that no longer matches it.
Nothing but this application writes the file, so there is nothing untrusted in
it to unpickle.

What this still does not give you is one job store across several machines.
Two hosts have two files, and a job accepted by one is unknown to the other.
That needs a shared database rather than a local one, and nothing here pretends
otherwise: `render.yaml` runs a single instance for exactly this reason.
"""

from __future__ import annotations

import json
import logging
import pickle
import sqlite3
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .. import __version__
from ..config import get_settings
from .analysis import AnalysisResult

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

MAX_JOBS = 50
"""How many jobs to remember.

Bounded because a finished job holds its whole result: an unbounded register of
analysis results is a memory leak with a polite name, and an unbounded file is
a disk that fills. Only finished jobs are evicted, oldest first, so a burst of
work can never discard something still running.
"""

PICKLE_PROTOCOL = 5
"""Pinned rather than `pickle.HIGHEST_PROTOCOL`.

The highest protocol depends on the interpreter that wrote it, so a file
written by a newer Python cannot be read by an older one. Protocol 5 has been
available since 3.8 and is understood by every version this runs on.
"""

PROGRESS_STEP = 0.01
"""How far progress must move before it is worth a write.

Detection reports once per tile and a large raster has thousands of them. The
figure is shown as a percentage, so writing every report to disk would be
thousands of writes to advance a number that has not visibly changed.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    stage        TEXT NOT NULL,
    progress     REAL NOT NULL,
    image_name   TEXT NOT NULL,
    boundary_name TEXT,
    parameters   TEXT NOT NULL,
    error        TEXT,
    result       BLOB,
    app_version  TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0
)
"""

MIGRATIONS = (
    "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
)
"""Statements that bring an older file up to the current schema.

Each one is run and its "duplicate column" complaint ignored, which is the
whole of the migration story a table this small needs. The alternative - a
version table and a runner to read it - is more moving parts than the thing
being migrated.
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cancelled_error() -> JobError:
    """Why there is no result, in the same shape as every other failure.

    Carried on the job so that asking for the result of a cancelled analysis
    is answered with what happened to it, rather than with the generic "not
    finished" that would be true of one still running.
    """
    return JobError(
        code="cancelled",
        message="This analysis was cancelled before it finished, so there is no result.",
        status_code=409,
    )


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
    cancel_requested: bool = False
    """Somebody has asked for this analysis to stop.

    Separate from the status, because asking is not the same as having
    stopped. A running analysis notices at its next checkpoint, which is one
    tile away; until then the job is still honestly "running".
    """
    created_at: datetime = field(default_factory=_now)
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
        return self.status in ("completed", "failed", "cancelled")


class JobStore:
    """A thread-safe, bounded register of jobs, durable when given a file.

    Analyses run in a worker thread while requests are served on the event
    loop, so every read and write goes through one lock. A job this process
    holds is handed out live: callers read it, and only the store mutates it.
    """

    def __init__(self, max_jobs: int = MAX_JOBS, path: Path | None = None) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._max_jobs = max_jobs
        self._db = self._open(path) if path is not None else None

    # --- the record -------------------------------------------------------

    def _open(self, path: Path) -> sqlite3.Connection | None:
        """Open the record, or carry on without one.

        A data directory that cannot be written to is a reason to lose jobs
        across a restart. It is not a reason to refuse to analyse anything,
        which is what raising here would amount to.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, check_same_thread=False)
            # WAL so a worker reading one job does not block the worker writing
            # progress to another, between processes as well as threads. Every
            # write is a few hundred bytes until a result arrives, so NORMAL
            # sync costs nothing measurable and still survives a process crash;
            # only losing power can lose the last write.
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute(SCHEMA)
            for statement in MIGRATIONS:
                try:
                    db.execute(statement)
                except sqlite3.OperationalError:
                    pass  # already applied; the column is there
            db.commit()
        except (sqlite3.Error, OSError) as error:
            log.warning("Jobs will not survive a restart: could not open %s (%s)", path, error)
            return None

        self._close_out_what_the_last_run_left(db)
        log.info("Job store backed by %s", path)
        return db

    def _close_out_what_the_last_run_left(self, db: sqlite3.Connection) -> None:
        """Fail jobs that were still in flight when a process died.

        A row left at "running" describes work no thread is doing any more.
        Left alone it would be polled forever and would count against the queue
        limit for the life of the file.

        This runs once per process, so where several workers share one file a
        worker restarting on its own will briefly mark another's live job
        interrupted. That corrects itself: the worker actually running it
        writes its whole state through on the next progress report, which is at
        most one tile away.
        """
        interrupted = json.dumps(
            {
                "code": "interrupted",
                "message": (
                    "The server restarted while this analysis was running. "
                    "Nothing partial was saved; submit the imagery again."
                ),
                "detail": {},
                "status_code": 409,
            }
        )
        try:
            cursor = db.execute(
                "UPDATE jobs SET status='failed', stage='failed', error=?, finished_at=? "
                "WHERE status IN ('queued', 'running')",
                (interrupted, _now().isoformat()),
            )
            db.commit()
        except sqlite3.Error as error:
            log.warning("Could not close out interrupted jobs: %s", error)
            return
        if cursor.rowcount:
            log.info("Marked %d job(s) interrupted by a previous run", cursor.rowcount)

    def _record_locked(self, job: Job) -> None:
        """Write the whole job. Called with the lock held.

        One upsert of every column rather than an update per field: the rows
        are small, the result is written exactly once because a finished job
        takes no further updates, and a single statement cannot leave the row
        describing a state the job was never in.
        """
        if self._db is None:
            return
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO jobs (id, created_at, started_at, finished_at, "
                "status, stage, progress, image_name, boundary_name, parameters, error, "
                "result, app_version, cancel_requested) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job.id,
                    job.created_at.isoformat(),
                    job.started_at.isoformat() if job.started_at else None,
                    job.finished_at.isoformat() if job.finished_at else None,
                    job.status,
                    job.stage,
                    job.progress,
                    job.image_name,
                    job.boundary_name,
                    json.dumps(job.parameters, default=str),
                    json.dumps(vars(job.error)) if job.error else None,
                    pickle.dumps(job.result, protocol=PICKLE_PROTOCOL) if job.result else None,
                    __version__,
                    int(job.cancel_requested),
                ),
            )
            self._db.commit()
        except (sqlite3.Error, pickle.PicklingError, TypeError) as error:
            # Losing durability for one job is worth strictly less than the
            # analysis itself, which is in memory and still answerable.
            log.warning("Could not record job %s: %s", job.id, error)

    def _restore_locked(self, job_id: str) -> Job | None:
        """Rebuild a job this process does not hold. Called with the lock held."""
        if self._db is None:
            return None
        try:
            row = self._db.execute(
                "SELECT created_at, started_at, finished_at, status, stage, progress, "
                "image_name, boundary_name, parameters, error, result, app_version, "
                "cancel_requested FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        except sqlite3.Error as error:
            log.warning("Could not read job %s: %s", job_id, error)
            return None
        if row is None:
            return None

        job = Job(
            id=job_id,
            image_name=row[6],
            boundary_name=row[7],
            parameters=json.loads(row[8]),
            status=row[3],
            stage=row[4],
            progress=row[5],
            created_at=datetime.fromisoformat(row[0]),
            started_at=datetime.fromisoformat(row[1]) if row[1] else None,
            finished_at=datetime.fromisoformat(row[2]) if row[2] else None,
            error=JobError(**json.loads(row[9])) if row[9] else None,
            cancel_requested=bool(row[12]),
        )
        job.result = self._unpickle(job_id, row[10], row[11])
        if job.status == "completed" and job.result is None:
            # Reporting "completed" and then having nothing to hand over is the
            # one answer worse than an error, because the client will ask again.
            job.status = "failed"
            job.stage = "failed"
            job.error = JobError(
                code="result_unreadable",
                message=(
                    "This analysis finished, but its result was stored by a "
                    "different version of the server and can no longer be read. "
                    "Submit the imagery again."
                ),
                status_code=409,
            )
        return job

    def _unpickle(self, job_id: str, blob: bytes | None, app_version: str) -> AnalysisResult | None:
        if blob is None:
            return None
        if app_version != __version__:
            # A pickle names the classes it holds by module and attribute. Read
            # back by a build where those have moved or changed shape, it
            # produces an object that is wrong in ways which only surface
            # later - worse than saying plainly that the result is gone.
            log.info("Ignoring result for job %s: written by version %s", job_id, app_version)
            return None
        try:
            return pickle.loads(blob)
        except Exception as error:  # noqa: BLE001 - any failure here means "no result"
            log.warning("Could not read the stored result for job %s: %s", job_id, error)
            return None

    # --- the register -----------------------------------------------------

    def create(self, *, image_name: str, boundary_name: str | None, parameters: dict) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            image_name=image_name,
            boundary_name=boundary_name,
            parameters=parameters,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._record_locked(job)
            self._evict_locked()
        log.info("Created job %s for %s", job.id, image_name)
        return job

    def get(self, job_id: str) -> Job | None:
        """The job as it stands, from memory when this process holds it.

        A job held by another worker is read from the file every time rather
        than kept. Caching it would mean answering the next poll with the
        progress that worker had reached when this one first looked.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            return job if job is not None else self._restore_locked(job_id)

    def mark_running(self, job_id: str) -> bool:
        """Start the clock, unless the job is already over.

        Returns whether the caller should go on to do the work. A job
        cancelled while it waited in the queue is already finished by the time
        a thread picks it up, and must not be dragged back to "running".
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_finished:
                return False
            job.status = "running"
            job.stage = "starting"
            job.started_at = _now()
            self._record_locked(job)
            return True

    def request_cancel(self, job_id: str) -> Job | None:
        """Ask for a job to stop. Returns it as it stands, or None if unknown.

        A job still queued is finished here and now: no thread has picked it
        up, so there is nothing to wait for and nothing to interrupt. A running
        one is only flagged - it is still honestly running until it reaches a
        checkpoint and stops.

        A job another worker is running is not in this process's memory, so the
        flag is written to the file instead and that worker reads it at its
        next checkpoint.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return self._request_cancel_on_record_locked(job_id)
            if job.is_finished:
                return job
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.stage = "cancelled"
                job.finished_at = _now()
                job.error = _cancelled_error()
            self._record_locked(job)
            return job

    def _request_cancel_on_record_locked(self, job_id: str) -> Job | None:
        job = self._restore_locked(job_id)
        if job is None or job.is_finished:
            return job
        job.cancel_requested = True
        self._record_locked(job)
        return job

    def cancel_requested(self, job_id: str) -> bool:
        """Whether this analysis has been asked to stop.

        Checked by the thread doing the work, so it reads the file when the
        flag is not already set here: the request may have arrived at a
        different worker than the one running the job.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.cancel_requested:
                return True
            if self._db is None:
                return False
            try:
                row = self._db.execute(
                    "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            except sqlite3.Error:
                return False
            if row is None or not row[0]:
                return False
            if job is not None:
                job.cancel_requested = True
            return True

    def mark_cancelled(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "cancelled"
            job.stage = "cancelled"
            job.error = _cancelled_error()
            job.finished_at = _now()
            self._record_locked(job)
        log.info("Job %s cancelled", job_id)

    def update_progress(self, job_id: str, stage: str, progress: float) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_finished:
                return
            worth_recording = stage != job.stage or progress - job.progress >= PROGRESS_STEP
            job.stage = stage
            job.progress = progress
            if worth_recording:
                self._record_locked(job)

    def mark_completed(self, job_id: str, result: AnalysisResult) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "completed"
            job.stage = "completed"
            job.progress = 1.0
            job.result = result
            job.finished_at = _now()
            self._record_locked(job)
        log.info("Job %s completed: %d crown(s)", job_id, len(result.crowns))

    def mark_failed(self, job_id: str, error: JobError) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.stage = "failed"
            job.error = error
            job.finished_at = _now()
            self._record_locked(job)
        log.info("Job %s failed: %s - %s", job_id, error.code, error.message)

    def count_queued(self) -> int:
        """How many jobs are accepted but not yet started.

        Read before accepting another: the queue is the part of the backlog
        nobody is working on yet, and the part that can grow without bound
        while the pool works through what it already has. Counted from the file
        where there is one, so several workers sharing it are held to one limit
        between them rather than to one limit each.
        """
        with self._lock:
            if self._db is not None:
                try:
                    row = self._db.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status = 'queued'"
                    ).fetchone()
                    return int(row[0])
                except sqlite3.Error as error:
                    log.warning("Could not count queued jobs: %s", error)
            return sum(1 for job in self._jobs.values() if job.status == "queued")

    def clear(self) -> None:
        """Forget every job. Used by tests to keep one case out of another's way."""
        with self._lock:
            self._jobs.clear()
            if self._db is None:
                return
            try:
                self._db.execute("DELETE FROM jobs")
                self._db.commit()
            except sqlite3.Error as error:
                log.warning("Could not clear the job record: %s", error)

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs until the store is back within budget."""
        while len(self._jobs) > self._max_jobs:
            victim = next((j for j in self._jobs.values() if j.is_finished), None)
            if victim is None:
                # Everything still running. Going over budget is the lesser
                # problem; discarding work in flight is the greater one.
                log.warning("Job store over budget with no finished job to evict")
                break
            del self._jobs[victim.id]
            log.info("Evicted job %s to stay within %d jobs", victim.id, self._max_jobs)
        self._prune_locked()

    def _prune_locked(self) -> None:
        """Hold the file to the same budget, on the same terms.

        Finished rows only, oldest first: a row for work still in flight is the
        one thing here that cannot be recreated by asking again.
        """
        if self._db is None:
            return
        try:
            stored = int(self._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            if stored <= self._max_jobs:
                return
            self._db.execute(
                "DELETE FROM jobs WHERE id IN ("
                "  SELECT id FROM jobs WHERE status IN ('completed', 'failed')"
                "  ORDER BY created_at ASC LIMIT ?"
                ")",
                (stored - self._max_jobs,),
            )
            self._db.commit()
        except sqlite3.Error as error:
            log.warning("Could not prune the job record: %s", error)


@lru_cache
def get_job_store() -> JobStore:
    """The process-wide job store, as a FastAPI dependency.

    Built on first use rather than at import, so the data directory comes from
    the settings the process was actually started with rather than from
    whatever was in the environment when the module happened to be imported.
    """
    return JobStore(path=get_settings().data_dir / "jobs.db")
