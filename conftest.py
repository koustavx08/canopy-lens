"""Makes the backend package importable when pytest runs from the repo root."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT / "backend", ROOT / "ml"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
