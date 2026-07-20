#!/usr/bin/env python3
"""
Sync the effects engine VERBATIM from a Music Assistant server checkout.

The hue-box rule is "1:1 copy, never a rewrite": the files in effects/hue_fx/
must be byte-identical to the provider files in the server repo. This script
copies them, records their sha256 hashes plus the source git commit in
effects/MANIFEST.sha256, and can verify an existing copy with --check.

Usage:
    python tools/sync_effects.py [--server-repo PATH] [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HUE_BOX_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_REPO = HUE_BOX_ROOT.parent / "server"
PROVIDER_REL = Path("music_assistant/providers/hue_entertainment")

# The complete effects engine: pure-stdlib Python, no MA imports (verified).
EFFECT_FILES = [
    "analyzer.py",
    "structure.py",
    "strobe_overlay.py",
    "palettes.py",
    "palettes.json",
    "constants.py",
]

EFFECTS_DIR = HUE_BOX_ROOT / "effects" / "hue_fx"
MANIFEST = HUE_BOX_ROOT / "effects" / "MANIFEST.sha256"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_commit(server_repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(server_repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def sync(server_repo: Path) -> int:
    src_dir = server_repo / PROVIDER_REL
    if not src_dir.is_dir():
        print(f"ERROR: provider dir not found: {src_dir}", file=sys.stderr)
        return 2
    EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    init_py = EFFECTS_DIR / "__init__.py"
    if not init_py.exists():
        init_py.write_text(
            '"""Verbatim copy of the MA hue_entertainment effects engine - do not edit here."""\n'
        )

    lines = [
        "# hue-box effects manifest - written by tools/sync_effects.py",
        f"# synced: {datetime.now(timezone.utc).isoformat()}",
        f"# source: {server_repo} @ {source_commit(server_repo)}",
    ]
    for name in EFFECT_FILES:
        src = src_dir / name
        if not src.is_file():
            print(f"ERROR: missing source file: {src}", file=sys.stderr)
            return 2
        shutil.copyfile(src, EFFECTS_DIR / name)
        lines.append(f"{sha256_of(src)}  hue_fx/{name}")
        print(f"synced  {name}")
    MANIFEST.write_text("\n".join(lines) + "\n")
    print(f"OK  manifest -> {MANIFEST}")
    return 0


def check(server_repo: Path) -> int:
    """Verify the local copies still match the server checkout byte-for-byte."""
    src_dir = server_repo / PROVIDER_REL
    bad = 0
    for name in EFFECT_FILES:
        src, dst = src_dir / name, EFFECTS_DIR / name
        if not dst.is_file():
            print(f"MISSING {name}")
            bad += 1
        elif not src.is_file():
            print(f"NO-SRC  {name}")
            bad += 1
        elif sha256_of(src) != sha256_of(dst):
            print(f"DRIFT   {name}  (server copy differs - re-run sync)")
            bad += 1
        else:
            print(f"ok      {name}")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-repo", type=Path, default=DEFAULT_SERVER_REPO)
    parser.add_argument("--check", action="store_true", help="verify instead of copy")
    args = parser.parse_args()
    return check(args.server_repo) if args.check else sync(args.server_repo)


if __name__ == "__main__":
    raise SystemExit(main())
