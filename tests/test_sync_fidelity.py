"""Fidelity guard: the engine copy must stay byte-identical to the MA provider."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT.parent / "server" / "music_assistant" / "providers" / "hue_entertainment"


def test_engine_in_sync_with_provider() -> None:
    if not SERVER.is_dir():
        pytest.skip("MA server checkout not present next to TuneThatHue")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sync_effects.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"engine drifted from the provider:\n{proc.stdout}{proc.stderr}"
