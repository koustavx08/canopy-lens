"""Runtime dependency probing.

Used by `/api/capabilities` so the UI can tell the user exactly which parts of
the pipeline are installed on the server they are talking to.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
from dataclasses import dataclass

_CHECKED = (
    "numpy",
    "cv2",
    "scipy",
    "skimage",
    "rasterio",
    "shapely",
    "pyproj",
    "fiona",
    "geopandas",
    "torch",
    "deepforest",
)

_DISTRIBUTIONS = {"cv2": "opencv-python-headless", "skimage": "scikit-image"}


@dataclass(slots=True)
class EngineAvailability:
    module: str
    installed: bool
    version: str | None = None


def _version_of(module_name: str) -> str | None:
    dist = _DISTRIBUTIONS.get(module_name, module_name)
    for candidate in (dist, module_name):
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
    module = importlib.import_module(module_name)
    return getattr(module, "__version__", None)


def probe_dependencies(modules: tuple[str, ...] = _CHECKED) -> list[EngineAvailability]:
    results: list[EngineAvailability] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception:
            results.append(EngineAvailability(module=name, installed=False))
            continue
        try:
            version = _version_of(name)
        except Exception:
            version = None
        results.append(EngineAvailability(module=name, installed=True, version=version))
    return results
