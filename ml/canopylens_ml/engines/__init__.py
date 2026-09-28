"""Concrete detection engines.

Importing this module registers every engine that ships with CanopyLens.
Engines are registered unconditionally; whether they can *run* is decided at
call time by `DetectionEngine.check()`, so a missing optional dependency
produces an explicit "engine unavailable" message rather than a silent gap.

Order matters: `PREFERENCE_ORDER` is what `engine="auto"` walks, best first.
The first engine that reports itself genuinely available is used. If none does,
the caller raises rather than falling back to invented detections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..registry import register_engine

if TYPE_CHECKING:  # imported for typing only; the runtime path stays lazy
    from ..base import DetectionEngine

# Each entry is a lazy factory path so that importing the registry never pulls
# in torch or downloads model weights.
_LAZY_ENGINES: dict[str, str] = {
    "classical": "canopylens_ml.engines.classical:ClassicalEngine",
    "deepforest": "canopylens_ml.engines.deepforest_engine:DeepForestEngine",
    "segmentation": "canopylens_ml.segmentation:ActiveContourSegmentationEngine",
}

# Best quality first. DeepForest is a model trained for this exact task;
# segmentation provides true instance polygons; classical is the dependable fallback.
PREFERENCE_ORDER: tuple[str, ...] = ("deepforest", "segmentation", "classical")


def _install() -> None:
    for name, dotted in _LAZY_ENGINES.items():
        module_name, attr = dotted.rsplit(":", 1)

        def factory(module_name: str = module_name, attr: str = attr):
            import importlib

            return getattr(importlib.import_module(module_name), attr)()

        register_engine(name, factory)


_install()


def resolve_engine(name: str = "auto") -> DetectionEngine:
    """Return an engine that reports itself genuinely runnable.

    "auto" walks PREFERENCE_ORDER and returns the first available engine. A
    named engine that is unavailable raises rather than quietly substituting a
    different one: a user who asked for DeepForest and got colour thresholding
    would be reading someone else's results.
    """
    from ..base import EngineUnavailableError
    from ..registry import available_engines, get_engine, registered_engines

    if name and name != "auto":
        if name not in registered_engines():
            raise EngineUnavailableError(
                f"Unknown detection engine {name!r}. Available: "
                + ", ".join(registered_engines())
            )
        engine = get_engine(name)
        ok, reason = engine.check()
        if not ok:
            raise EngineUnavailableError(
                f"Detection engine {name!r} cannot run here: {reason}"
            )
        return engine

    for candidate in PREFERENCE_ORDER:
        if candidate not in registered_engines():
            continue
        engine = get_engine(candidate)
        try:
            ok, _ = engine.check()
        except Exception:
            continue
        if ok:
            return engine

    reasons = "; ".join(
        f"{info.name}: {info.reason}" for info in available_engines() if not info.available
    )
    raise EngineUnavailableError(
        "No detection engine is available in this environment. " + (reasons or "")
    )
