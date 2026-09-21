"""Who may call, and how often.

Two independent limits, because they answer different questions. The API key
answers "may this caller use the service at all"; the rate limit answers "is
this caller asking faster than the service should answer". A deployment can
want either without the other: an internal instance behind a VPN needs the
rate limit and no key, a public demo needs the key and the limit both.

Both are off by default. That is deliberate: a laptop running `uvicorn` for
the afternoon should not need credentials, and a developer who has to invent
one to see the UI will pick the same one everybody else picks. The cost of the
default is that an open instance is open, so `/api/health/ready` reports an
unauthenticated server as degraded once the environment is no longer
"development", where it is a fact worth seeing rather than a mistake.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import OrderedDict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings, get_settings
from .errors import RateLimitedError, UnauthorizedError

log = logging.getLogger(__name__)

EXEMPT_PREFIXES = ("/health", "/api/health", "/api/metrics")
"""Paths the rate limit does not apply to.

An orchestrator polls the probes on a fixed schedule it will not slow down for
a 429, and a metrics scrape that gets rate-limited takes away the one view of
the server that would have explained why.
"""

MAX_TRACKED_CLIENTS = 10_000
"""How many callers the limiter remembers at once.

The buckets are a dictionary keyed by address, and an address is something the
caller influences. Bounded, so a spray of requests from many sources costs a
fixed amount of memory; the oldest bucket is dropped first, which is the one
whose owner has been quiet longest and whose allowance has refilled anyway.
"""


def client_address(request: Request, hops: int) -> str:
    """The address to hold responsible for this request.

    `X-Forwarded-For` is a list that each proxy appends to, so the entry a
    trusted proxy wrote is `hops` from the end. Everything before it was
    supplied by the caller and can say anything at all - which is why the
    header is read only as far back as there are proxies known to be in front,
    and ignored entirely when there are none.
    """
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def require_api_key(request: Request) -> None:
    """Reject a caller without a configured key, when any key is configured.

    Accepts `X-API-Key: <key>` or `Authorization: Bearer <key>`; the second
    because it is what most HTTP clients already know how to send.
    """
    settings = get_settings()
    accepted = settings.api_key_set
    if not accepted:
        return

    presented = request.headers.get("x-api-key") or _bearer(request)
    # compare_digest against every configured key rather than a set lookup:
    # the time a set lookup takes depends on the key it was given, and that
    # is a measurement an attacker can make.
    if presented and any(secrets.compare_digest(presented, key) for key in accepted):
        return

    raise UnauthorizedError(
        "This server requires an API key. Send it as the X-API-Key header, or "
        "as a bearer token in Authorization.",
        detail={"presented": bool(presented)},
    )


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A token bucket per caller, refilled continuously.

    A bucket rather than a fixed window: a window lets a caller spend its whole
    allowance in the last second of one window and again in the first second of
    the next, which is the burst the limit existed to prevent. The bucket holds
    a minute's worth, so an interface that opens several requests at once still
    loads, and refills at the configured rate, so a client that keeps polling
    settles at exactly that rate rather than being cut off.
    """

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.capacity = float(settings.rate_limit_per_minute)
        self.refill_per_second = self.capacity / 60.0
        self.hops = settings.trusted_proxy_hops
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    async def dispatch(self, request: Request, call_next):
        if self.capacity <= 0 or request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        caller = client_address(request, self.hops)
        allowed, retry_after = self._take_token(caller)
        if not allowed:
            log.warning("Rate limited %s on %s %s", caller, request.method, request.url.path)
            error = RateLimitedError(
                "Too many requests. This server accepts "
                f"{int(self.capacity)} requests per minute from one caller.",
                detail={"retry_after_seconds": retry_after},
            )
            return JSONResponse(
                status_code=error.status_code,
                content=error.to_payload(),
                # Returned rather than raised: an exception from a middleware
                # is outside the handlers registered on the app, so it would
                # come back as a bare 500 in a shape no client expects.
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    def _take_token(self, caller: str) -> tuple[bool, int]:
        now = time.monotonic()
        tokens, last_seen = self._buckets.get(caller, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last_seen) * self.refill_per_second)

        if tokens < 1.0:
            self._buckets[caller] = (tokens, now)
            self._buckets.move_to_end(caller)
            return False, max(1, int((1.0 - tokens) / self.refill_per_second) + 1)

        self._buckets[caller] = (tokens - 1.0, now)
        self._buckets.move_to_end(caller)
        while len(self._buckets) > MAX_TRACKED_CLIENTS:
            self._buckets.popitem(last=False)
        return True, 0
