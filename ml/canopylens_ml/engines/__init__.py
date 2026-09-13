"""Concrete detection engines.

Importing this module registers every engine that ships with CanopyLens.
Engines are registered unconditionally; whether they can *run* is decided at
call time by `DetectionEngine.check()`, so a missing optional dependency
produces an explicit "engine unavailable" message rather than a silent gap.
"""

from __future__ import annotations

from ..registry import register_engine

# Engines are added here as they land. Each entry is a lazy factory so that
# importing the registry never pulls in torch or model weights.
_LAZY_ENGINES: dict[str, str] = {}


def _install() -> None:
    for name, dotted in _LAZY_ENGINES.items():
        module_name, attr = dotted.rsplit(":", 1)

        def factory(module_name: str = module_name, attr: str = attr):
            import importlib

            return getattr(importlib.import_module(module_name), attr)()

        register_engine(name, factory)


_install()
