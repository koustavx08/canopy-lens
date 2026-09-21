"""Analysis jobs: submit imagery, poll, collect results.

An analysis is not a request-shaped piece of work. A large mosaic can take
minutes, during which a synchronous endpoint would give the browser no progress
and a reverse proxy every excuse to time out and throw away work that had nearly
finished. So the flow is in three parts:

    POST /api/analyze          -> 202, a job id
    GET  /api/jobs/{job_id}    -> status, stage, progress
    GET  /api/results/{job_id} -> the measured result, once it exists

The uploaded files are deleted as soon as the analysis has read them. The result
stays in memory; CSV and GeoJSON are rendered from it on request rather than
written to disk, so there is nothing to clean up afterwards.

Analyses run on a pool of `CANOPYLENS_MAX_CONCURRENT_ANALYSES` threads rather
than on the server's general worker threads. Detection saturates a core, so
starting every upload at once finishes none of them sooner - it only makes each
one slower and leaves no thread free to answer the progress polls or the health
check that keeps the service alive. Work beyond the limit waits as `queued`,
which is a state the job model already has and the interface already shows.
"""

from __future__ import annotations

import atexit
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from canopylens_ml import registered_engines
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from ..config import Settings, get_settings
from ..errors import (
    CanopyLensError,
    InvalidParameterError,
    QueueFullError,
    ResourceNotFoundError,
)
from ..schemas import ErrorResponse, JobAcceptedResponse, JobStatusResponse
from ..services import raster
from ..services.analysis import analyze_image
from ..services.boundary import SUPPORTED_EXTENSIONS as BOUNDARY_EXTENSIONS
from ..services.export import export_csv, export_geojson
from ..services.jobs import Job, JobError, JobStore, get_job_store
from ..services.results import job_status_payload, result_payload
from ..services.uploads import save_upload

log = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])


class JobNotFinishedError(CanopyLensError):
    status_code = 409
    code = "job_not_finished"


@lru_cache
def analysis_pool() -> ThreadPoolExecutor:
    """The bounded pool every analysis runs on.

    One pool for the process, created on first use so that a test which
    overrides the settings gets the size it asked for.
    """
    workers = max(1, get_settings().max_concurrent_analyses)
    log.info("Analysis pool: %d concurrent analysis(es)", workers)
    return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")


def shutdown_analysis_pool() -> None:
    """Stop accepting analyses and drop any that have not started.

    Called on shutdown. Work already running is left to finish: it holds an
    open raster and a half-written job, and killing it mid-read would leave
    neither cleaned up.
    """
    if analysis_pool.cache_info().currsize:
        analysis_pool().shutdown(wait=False, cancel_futures=True)
        analysis_pool.cache_clear()


# A worker process that is terminated rather than shut down cleanly still has
# to release its threads, or the interpreter hangs on exit.
atexit.register(shutdown_analysis_pool)


def _refuse_if_the_queue_is_full(store: JobStore, settings: Settings) -> None:
    """Say no before the upload is read, not after.

    Checked first thing, so a refused submission never costs the server the
    time and disk of receiving an image it was not going to analyse. The count
    can be stale by the time the job is created - another request may take the
    last place in between - but the limit is a backstop against a growing
    backlog, not an exact quota, and being one over it briefly costs nothing.
    """
    waiting = store.count_queued()
    if waiting >= settings.max_queued_analyses:
        raise QueueFullError(
            f"{waiting} analyses are already waiting to start. Try again once "
            "the server has worked through them.",
            detail={"queued": waiting, "limit": settings.max_queued_analyses},
        )


MAX_MIN_CROWN_AREA_M2 = 100_000.0
"""Upper bound on the crown-area filter: ten hectares, which is not a crown.

A bound exists so that a typo does not silently discard every detection and
return an honest, useless zero.
"""


@router.post(
    "/analyze",
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Submit imagery for crown detection",
    responses={
        413: {"model": ErrorResponse, "description": "A file exceeds the upload limit"},
        415: {"model": ErrorResponse, "description": "Unsupported image or boundary format"},
        422: {"model": ErrorResponse, "description": "A parameter is out of range"},
    },
)
def start_analysis(
    image: UploadFile = File(..., description="GeoTIFF, PNG or JPEG imagery to analyse"),
    boundary: UploadFile | None = File(
        None, description="Optional KML, KMZ or GeoJSON boundary restricting the analysis"
    ),
    confidence_threshold: float | None = Form(
        None, ge=0.0, le=1.0, description="Detections scoring below this are discarded"
    ),
    min_crown_area_m2: float | None = Form(
        None,
        ge=0.0,
        le=MAX_MIN_CROWN_AREA_M2,
        description="Smallest crown to keep, in square metres. Ignored for imagery "
        "with no georeferencing, which has no square metres to measure.",
    ),
    engine: str | None = Form(None, description="Detection engine, or 'auto'"),
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> JobAcceptedResponse:
    """Accept imagery, start an analysis, and return the job to poll.

    Validation happens before any work is scheduled: an unsupported extension,
    an oversized file or an unknown engine is answered immediately rather than
    becoming a job that exists only to fail.
    """
    _refuse_if_the_queue_is_full(store, settings)
    resolved_engine = _validate_engine(engine if engine is not None else settings.default_engine)
    confidence = (
        settings.default_confidence if confidence_threshold is None else confidence_threshold
    )
    min_area = (
        settings.default_min_crown_area_m2 if min_crown_area_m2 is None else min_crown_area_m2
    )

    image_path = save_upload(
        image,
        settings.upload_dir,
        raster.SUPPORTED_EXTENSIONS,
        settings.max_upload_mb * 1024 * 1024,
        kind="image",
    )

    boundary_path: Path | None = None
    if _is_supplied(boundary):
        try:
            boundary_path = save_upload(
                boundary,
                settings.upload_dir,
                BOUNDARY_EXTENSIONS,
                settings.max_boundary_upload_mb * 1024 * 1024,
                kind="boundary",
            )
        except Exception:
            # The image is already on disk. A rejected boundary must not leave
            # it there with no job that will ever come to clean it up.
            image_path.unlink(missing_ok=True)
            raise

    parameters = {
        "confidence_threshold": confidence,
        "min_crown_area_m2": min_area,
        "engine": resolved_engine,
        "tile_size": settings.tile_size,
        "tile_overlap": settings.tile_overlap,
    }
    display_name = _display_name(image.filename)
    job = store.create(
        image_name=display_name,
        boundary_name=_display_name(boundary.filename) if boundary_path else None,
        parameters=parameters,
    )

    future = analysis_pool().submit(
        _run_analysis, store, job.id, image_path, boundary_path, parameters, display_name
    )
    # A future nobody awaits swallows whatever it raises. `_run_analysis`
    # handles its own failures, so anything arriving here escaped even that -
    # which would leave the job reading "running" forever with no trace of why.
    future.add_done_callback(_log_if_the_thread_crashed)

    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status,
        status_url=f"/api/jobs/{job.id}",
        result_url=f"/api/results/{job.id}",
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Progress of an analysis job",
    responses={404: {"model": ErrorResponse, "description": "No such job"}},
)
def get_job(job_id: str, store: JobStore = Depends(get_job_store)) -> JobStatusResponse:
    """Status, stage and progress. Cheap enough to poll once a second.

    `progress` counts tiles completed, not seconds elapsed, so it reflects work
    done rather than time spent. It does not carry the detections: a client
    watching a progress bar should not be re-downloading polygons to learn that
    nothing has finished yet.
    """
    return JobStatusResponse(**job_status_payload(_require_job(store, job_id)))


@router.get(
    "/results/{job_id}",
    summary="The measured result of a completed job",
    responses={
        404: {"model": ErrorResponse, "description": "No such job"},
        409: {"model": ErrorResponse, "description": "The job has not finished"},
    },
)
def get_result(job_id: str, store: JobStore = Depends(get_job_store)) -> dict:
    """Image metadata, boundary metadata, summary, detections and limitations."""
    return result_payload(_require_completed(store, job_id))


@router.get(
    "/results/{job_id}/csv",
    summary="Download the crowns as CSV",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}}, "description": "One row per crown"},
        404: {"model": ErrorResponse, "description": "No such job"},
        409: {"model": ErrorResponse, "description": "The job has not finished"},
    },
)
def get_result_csv(job_id: str, store: JobStore = Depends(get_job_store)) -> Response:
    job = _require_completed(store, job_id)
    return _download(export_csv(job.result), "text/csv; charset=utf-8", f"crowns-{job.id}.csv")


@router.get(
    "/results/{job_id}/geojson",
    summary="Download the crowns as GeoJSON",
    response_class=Response,
    responses={
        200: {"content": {"application/geo+json": {}}, "description": "One feature per crown"},
        404: {"model": ErrorResponse, "description": "No such job"},
        409: {"model": ErrorResponse, "description": "The job has not finished"},
    },
)
def get_result_geojson(job_id: str, store: JobStore = Depends(get_job_store)) -> Response:
    job = _require_completed(store, job_id)
    return _download(
        export_geojson(job.result, indent=2),
        "application/geo+json",
        f"crowns-{job.id}.geojson",
    )


# --- running the job ------------------------------------------------------


def _log_if_the_thread_crashed(future) -> None:
    error = future.exception()
    if error is not None:
        log.error("Analysis thread crashed outside its own error handling", exc_info=error)


def _run_analysis(
    store: JobStore,
    job_id: str,
    image_path: Path,
    boundary_path: Path | None,
    parameters: dict,
    source_name: str,
) -> None:
    """Run one analysis to completion and record how it ended.

    This runs in a worker thread after the response has been sent, so nothing
    here may raise: an exception would be lost and the job would sit at
    "running" forever. Every outcome is written to the store.
    """
    store.mark_running(job_id)
    try:
        result = analyze_image(
            image_path,
            boundary_path,
            confidence_threshold=parameters["confidence_threshold"],
            min_crown_area_m2=parameters["min_crown_area_m2"] or None,
            engine=parameters["engine"],
            tile_size=parameters["tile_size"],
            tile_overlap=parameters["tile_overlap"],
            on_progress=lambda stage, fraction: store.update_progress(job_id, stage, fraction),
            source_name=source_name,
        )
    except CanopyLensError as exc:
        # Already phrased for a user: it names what is wrong with the file or
        # the request, so it is passed through unchanged.
        store.mark_failed(
            job_id,
            JobError(
                code=exc.code,
                message=exc.message,
                detail=exc.detail,
                status_code=exc.status_code,
            ),
        )
    except Exception:
        log.exception("Job %s failed unexpectedly", job_id)
        store.mark_failed(
            job_id,
            JobError(
                code="analysis_failed",
                message=(
                    "The analysis stopped unexpectedly while processing this "
                    "image. Nothing partial was saved. The server log holds the "
                    "details."
                ),
            ),
        )
    else:
        store.mark_completed(job_id, result)
    finally:
        # The pipeline has read what it needs; the result lives in memory.
        # Keeping the upload would leave the disk filling with imagery nobody
        # will ask for again.
        image_path.unlink(missing_ok=True)
        if boundary_path is not None:
            boundary_path.unlink(missing_ok=True)


# --- helpers --------------------------------------------------------------


def _require_job(store: JobStore, job_id: str) -> Job:
    """Look up a job, or say plainly that there is not one.

    The id indexes a dictionary and is never joined to a path, so a traversal
    attempt is simply a key that is not there.
    """
    job = store.get(job_id)
    if job is None:
        raise ResourceNotFoundError(
            "No analysis job with that id. Jobs are held in memory, so a "
            "restart of the server clears them and an id from a previous run "
            "will not be found.",
            detail={"job_id": job_id},
        )
    return job


def _require_completed(store: JobStore, job_id: str) -> Job:
    job = _require_job(store, job_id)
    if job.status == "completed":
        return job
    if job.status == "failed" and job.error is not None:
        raise _restored(job.error)
    raise JobNotFinishedError(
        f"This analysis is {job.status}. Poll /api/jobs/{job_id} until its "
        "status is 'completed', then request the result.",
        detail={"job_id": job_id, "status": job.status, "progress": job.progress},
    )


def _restored(error: JobError) -> CanopyLensError:
    """Re-raise a background failure as the error it was when it happened.

    The pipeline's own code, message and status are kept, so asking for the
    result of a failed job answers exactly what a synchronous call would have.
    """
    exc = CanopyLensError(error.message, detail=error.detail)
    exc.code = error.code
    exc.status_code = error.status_code
    return exc


def _download(body: str, media_type: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _validate_engine(name: str) -> str:
    """Accept 'auto' or a registered engine name; refuse anything else by name.

    Whether an engine can actually *run* is settled by the pipeline, which can
    say why it cannot. This only rejects names that do not exist.
    """
    if name == "auto" or name in registered_engines():
        return name
    raise InvalidParameterError(
        f"'{name}' is not a detection engine on this server.",
        detail={"engine": name, "available": ["auto", *registered_engines()]},
    )


def _is_supplied(upload: UploadFile | None) -> bool:
    """True when a boundary was actually attached.

    A browser that includes an empty file input sends a part with an empty
    filename. Treating that as a boundary would fail the analysis over a field
    the user never filled in.
    """
    return upload is not None and bool(upload.filename) and upload.filename != "undefined"


def _display_name(filename: str | None) -> str:
    """A filename safe to echo back, with any path the client sent removed.

    Only ever shown, never opened: the stored path is generated in
    `services.uploads` and does not use this value.
    """
    if not filename:
        return "upload"
    return Path(str(filename).replace("\\", "/")).name or "upload"
