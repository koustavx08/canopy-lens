"""Minimal, dependency-free logging setup shared by API and CLI scripts."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_configured = False


def _utf8_stdout():
    """stdout that never raises on an unencodable character.

    Third-party libraries log emoji - Lightning, pulled in by DeepForest, opens
    a tip with one. On a console using a legacy code page (the Windows default)
    encoding that raises inside the logging machinery, which prints a traceback
    of its own and drops the record. Replacing the character is strictly better
    than losing the line and the ones that follow it.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # A stream that is not a reconfigurable text wrapper - a pytest capture
        # object, for instance. Nothing to fix and nothing to fail over.
        pass
    return sys.stdout


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(_utf8_stdout())
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("rasterio").setLevel(logging.WARNING)
    logging.getLogger("fiona").setLevel(logging.WARNING)
    _configured = True
