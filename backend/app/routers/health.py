"""Liveness, readiness, capabilities and metrics.

Liveness and readiness are different questions and are answered separately.
`/health` asks whether the process is alive, and an orchestrator restarts it
when the answer stops coming. `/health/ready` asks whether it can actually do
the work, which is a question about the disk, the engines and the
configuration - a server that answers it truthfully is one whose deployment
problems are visible before a user finds them.

Restarting a process that is alive but not ready almost never helps: a data
directory that cannot be written to will not be writable for the next process
either. So readiness is reported and liveness is not conflated with it.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Response

from .. import __version__
from ..config import Settings, get_settings
from ..observability import METRICS
from ..schemas import (
    CapabilitiesResponse,
    HealthResponse,
    ReadinessCheck,
    ReadinessResponse,
)
from ..security import require_api_key
from ..services.capabilities import build_capabilities
from ..services.jobs import JobStore, get_job_store

log = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Whether this instance can actually do the work",
    responses={503: {"model": ReadinessResponse, "description": "The service cannot analyse"}},
)
def ready(
    response: Response,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> ReadinessResponse:
    """Check the things a deployment gets wrong, and say which one it got wrong.

    Returns 503 only when something critical failed, because that is the signal
    a load balancer acts on by taking the instance out of rotation. An open API
    or a job store with nowhere to write are real problems worth reporting, but
    the instance can still analyse imagery, and pulling it out of rotation for
    them would turn a warning into an outage.
    """
    checks = [
        _data_directory_is_writable(settings),
        _an_engine_can_run(),
        _jobs_will_survive_a_restart(store),
        _the_door_is_closed(settings),
    ]
    failed_critical = any(not check.ok and check.critical for check in checks)
    status = "not_ready" if failed_critical else (
        "degraded" if any(not check.ok for check in checks) else "ready"
    )
    if failed_critical:
        response.status_code = 503
        log.warning("Readiness: %s", "; ".join(c.detail for c in checks if not c.ok))
    return ReadinessResponse(
        status=status,
        version=__version__,
        environment=settings.environment,
        checks=checks,
    )


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="What this server instance can actually do",
)
def capabilities(settings: Settings = Depends(get_settings)) -> CapabilitiesResponse:
    return build_capabilities(settings, __version__)


@router.get(
    "/metrics",
    summary="Counters in the Prometheus text format",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}, "description": "Exposition format"}},
    # Guarded like the working endpoints where keys are configured at all. The
    # numbers say how much work the instance is doing and which requests are
    # failing, which is not something to hand to anyone who finds the URL.
    dependencies=[Depends(require_api_key)],
)
def metrics(store: JobStore = Depends(get_job_store)) -> Response:
    return Response(
        content=METRICS.render(queued=store.count_queued()),
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


# --- the individual checks ------------------------------------------------


def _data_directory_is_writable(settings: Settings) -> ReadinessCheck:
    """Actually write, rather than asking the filesystem whether we could.

    A permission bit that says yes and a disk that is full disagree, and it is
    the disk that decides what happens to the next upload.
    """
    probe = settings.upload_dir / f".readiness-{os.getpid()}"
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as error:
        return ReadinessCheck(
            name="data_directory_writable",
            ok=False,
            detail=f"Cannot write to {settings.upload_dir}: {error}",
            critical=True,
        )
    return ReadinessCheck(
        name="data_directory_writable",
        ok=True,
        detail=f"{settings.upload_dir} accepted a test file",
        critical=True,
    )


def _an_engine_can_run() -> ReadinessCheck:
    from canopylens_ml import available_engines

    runnable = [e.name for e in available_engines() if e.available]
    if not runnable:
        return ReadinessCheck(
            name="detection_engine",
            ok=False,
            detail="No detection engine reports itself available in this process.",
            critical=True,
        )
    return ReadinessCheck(
        name="detection_engine",
        ok=True,
        detail=f"Available: {', '.join(runnable)}",
        critical=True,
    )


def _jobs_will_survive_a_restart(store: JobStore) -> ReadinessCheck:
    if not store.is_durable:
        return ReadinessCheck(
            name="job_record",
            ok=False,
            detail=(
                "The job record could not be opened, so jobs are held in memory "
                "alone and a restart will lose them. The server log says why."
            ),
            critical=False,
        )
    return ReadinessCheck(
        name="job_record",
        ok=True,
        detail="Jobs are recorded on disk and survive a restart.",
        critical=False,
    )


def _the_door_is_closed(settings: Settings) -> ReadinessCheck:
    """An open API is a fact worth seeing once the environment is not a laptop."""
    if settings.api_key_set or settings.environment == "development":
        return ReadinessCheck(
            name="access_control",
            ok=True,
            detail=(
                "An API key is required."
                if settings.api_key_set
                else "Open, which is the default for a development environment."
            ),
            critical=False,
        )
    return ReadinessCheck(
        name="access_control",
        ok=False,
        detail=(
            f"CANOPYLENS_API_KEYS is unset in the '{settings.environment}' "
            "environment, so anyone who finds this URL can spend its CPU."
        ),
        critical=False,
    )
