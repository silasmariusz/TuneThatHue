"""Path wiring for the test suite - mirrors the daemon's import layout."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for sub in ("python", "tools", "effects"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# The engine imports `hue_entertainment`; the venv has the real library
# (editable install), outside it the pystub provides the same symbols.
try:
    import hue_entertainment  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(ROOT / "pystub"))
