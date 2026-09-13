"""Liveness and capability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import __version__
from ..config import Settings, get_settings
from ..schemas import CapabilitiesResponse, HealthResponse
from ..services.capabilities import build_capabilities

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="What this server instance can actually do",
)
def capabilities(settings: Settings = Depends(get_settings)) -> CapabilitiesResponse:
    return build_capabilities(settings, __version__)
