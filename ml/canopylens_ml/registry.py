"""Engine registry.

Engines register themselves on import of `canopylens_ml.engines`. The registry
holds factories (not instances) so that importing the registry never triggers
a heavy model load.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import DetectionEngine, EngineInfo

_FACTORIES: dict[str, Callable[[], DetectionEngine]] = {}
_INSTANCES: dict[str, DetectionEngine] = {}


def register_engine(name: str, factory: Callable[[], DetectionEngine]) -> None:
    _FACTORIES[name] = factory


def registered_engines() -> list[str]:
    return sorted(_FACTORIES)


def get_engine(name: str) -> DetectionEngine:
    if name not in _FACTORIES:
        raise KeyError(f"Unknown detection engine: {name!r}. Known: {registered_engines()}")
    if name not in _INSTANCES:
        _INSTANCES[name] = _FACTORIES[name]()
    return _INSTANCES[name]


def available_engines() -> list[EngineInfo]:
    """Instantiate every registered engine and report its real availability."""
    infos: list[EngineInfo] = []
    for name in registered_engines():
        try:
            infos.append(get_engine(name).info())
        except Exception as exc:  # an engine that cannot even construct is unavailable
            infos.append(
                EngineInfo(
                    name=name,
                    title=name,
                    description="",
                    available=False,
                    reason=f"Engine failed to initialise: {exc}",
                )
            )
    return infos
