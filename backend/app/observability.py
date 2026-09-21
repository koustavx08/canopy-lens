"""Seeing what the server is doing: request ids, an access log, and counters.

Three things that answer the three questions asked of a service that has
started misbehaving. *Which request was that?* - every log line carries the id
of the request that produced it, and the response carries it back, so a user
reporting a failure hands over something that finds the exact lines. *What is
it doing?* - one access line per request with its outcome and how long it took.
*Is it getting worse?* - counters a scraper can watch over time.

None of this needs a dependency. The counters are a dictionary and the exposure
format is text; a metrics client library would be a package to install, pin and
upgrade in exchange for the twenty lines below.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from threading import Lock

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("canopylens.access")

REQUEST_ID_HEADER = "X-Request-ID"

PROBE_PREFIXES = ("/health", "/api/health", "/api/metrics")
"""Paths whose access lines are logged at DEBUG rather than INFO.

An orchestrator polls these every few seconds forever. At INFO they would be
most of the log, and the requests worth reading would be the hard ones to find.
"""

current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")
"""The id of the request being handled on this task, for the log filter.

A context variable rather than a thread local: requests are served on an event
loop where one thread handles many of them, and a thread local would hand a
request the id of whichever one it interleaved with.
"""


class RequestIdFilter(logging.Filter):
    """Puts `request_id` on every record so the format string can use it.

    A filter rather than a formatter, because records made outside a request -
    startup, shutdown, a worker thread - must still carry the attribute or the
    formatter raises while trying to report something else.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Gives each request an id, times it, and counts how it ended."""

    async def dispatch(self, request: Request, call_next):
        # An id from in front is kept rather than replaced: a proxy that
        # already assigned one is the only place the two sides of a trace can
        # be joined up. Length-capped, because it is echoed in a header and
        # written to the log, and a caller chooses it.
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()[:64]
        request_id = incoming or uuid.uuid4().hex[:16]
        token = current_request_id.set(request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            # In `finally` so that a request which raised is still counted and
            # still logged. An endpoint that fails is the one worth seeing.
            METRICS.observe_request(
                method=request.method,
                endpoint=_route_of(request),
                status=status,
                seconds=elapsed,
            )
            _log_access(request, status, elapsed)
            current_request_id.reset(token)


def _log_access(request: Request, status: int, seconds: float) -> None:
    level = logging.DEBUG if request.url.path.startswith(PROBE_PREFIXES) else logging.INFO
    log.log(
        level,
        '%s %s -> %d in %.1fms',
        request.method,
        request.url.path,
        status,
        seconds * 1000,
    )


def _route_of(request: Request) -> str:
    """The route pattern, not the URL.

    `/api/jobs/{job_id}` rather than the id itself: a label that takes a new
    value for every job would give the metrics store one time series per
    analysis, which is how a scraper is brought down by the thing it monitors.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class Metrics:
    """Counters, kept in a dictionary behind a lock.

    Process-local, like everything else here: with one instance that is the
    whole picture, and with several the scraper sums them, which is what a
    scraper is for.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._duration_sum: dict[str, float] = defaultdict(float)
        self._duration_count: dict[str, int] = defaultdict(int)
        self._analyses: dict[str, int] = defaultdict(int)
        self._running = 0
        self.started_at = time.time()

    def observe_request(self, *, method: str, endpoint: str, status: int, seconds: float) -> None:
        with self._lock:
            # Counted by class rather than by exact code: the question asked of
            # this number is "how much is failing", and one series per code
            # spreads that answer over a dozen of them.
            self._requests[(method, endpoint, f"{status // 100}xx")] += 1
            self._duration_sum[endpoint] += seconds
            self._duration_count[endpoint] += 1

    def analysis_started(self) -> None:
        with self._lock:
            self._analyses["started"] += 1
            self._running += 1

    def analysis_ended(self, outcome: str) -> None:
        with self._lock:
            self._analyses[outcome] += 1
            self._running = max(0, self._running - 1)

    def render(self, *, queued: int) -> str:
        """The counters in the Prometheus text exposition format."""
        with self._lock:
            lines = [
                "# HELP canopylens_requests_total HTTP requests handled.",
                "# TYPE canopylens_requests_total counter",
            ]
            for (method, endpoint, status), count in sorted(self._requests.items()):
                labels = f'method="{method}",endpoint="{_escape(endpoint)}",status="{status}"'
                lines.append(f"canopylens_requests_total{{{labels}}} {count}")

            lines += [
                "# HELP canopylens_request_duration_seconds Time spent handling requests.",
                "# TYPE canopylens_request_duration_seconds summary",
            ]
            for endpoint, total in sorted(self._duration_sum.items()):
                label = f'endpoint="{_escape(endpoint)}"'
                lines.append(f"canopylens_request_duration_seconds_sum{{{label}}} {total:.6f}")
                lines.append(
                    f"canopylens_request_duration_seconds_count{{{label}}} "
                    f"{self._duration_count[endpoint]}"
                )

            lines += [
                "# HELP canopylens_analyses_total Analyses by how they ended.",
                "# TYPE canopylens_analyses_total counter",
            ]
            for outcome in ("started", "completed", "failed", "cancelled"):
                lines.append(
                    f'canopylens_analyses_total{{outcome="{outcome}"}} '
                    f"{self._analyses[outcome]}"
                )

            lines += [
                "# HELP canopylens_analyses_running Analyses being worked on right now.",
                "# TYPE canopylens_analyses_running gauge",
                f"canopylens_analyses_running {self._running}",
                "# HELP canopylens_analyses_queued Analyses accepted and not yet started.",
                "# TYPE canopylens_analyses_queued gauge",
                f"canopylens_analyses_queued {queued}",
                "# HELP canopylens_process_start_time_seconds Start, in seconds since the epoch.",
                "# TYPE canopylens_process_start_time_seconds gauge",
                f"canopylens_process_start_time_seconds {self.started_at:.3f}",
            ]
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    """Label values are quoted, so a quote or backslash inside one must not be."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


METRICS = Metrics()
"""The process-wide counters.

A module-level object rather than a dependency: it is written to from the
middleware and from the analysis threads, neither of which is holding a FastAPI
request scope to resolve one from.
"""
