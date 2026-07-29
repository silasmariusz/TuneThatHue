#!/usr/bin/env python3
"""
Move the effects engine between Music Assistant and TuneThatHue, VERBATIM.

The point of this project is that the engine is the *same files* on both sides:
develop an effect or a preset here, where a restart takes a second and the box
is right in front of you, then move the changed files straight back into the
Music Assistant provider - no porting, no rewriting, no diff-by-hand.

So the copy goes both ways:

    pull    Music Assistant  ->  TuneThatHue   (take their changes)
    push    TuneThatHue      ->  Music Assistant (ship what you developed here)

MANIFEST.sha256 records the hash of every file as of the last sync, which is
what makes this safe: comparing both sides against it says who changed what,
so a pull can never silently throw away work done here, and a push can never
silently throw away work done in the provider.

Usage:
    python tools/sync_effects.py                 # show status (safe, read-only)
    python tools/sync_effects.py --pull          # provider -> here
    python tools/sync_effects.py --push          # here -> provider
    python tools/sync_effects.py --diff          # show what actually differs
    python tools/sync_effects.py --check         # exit 1 if anything is out of sync
    ... [--server-repo PATH] [--force]
"""

from __future__ import annotations

import argparse
import difflib
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

# Per-file state, worked out from (provider, here, manifest).
IN_SYNC = "in sync"
THEIRS = "changed in the provider"
OURS = "changed here"
BOTH = "changed on BOTH sides"
NEW_HERE = "missing in the provider"
NEW_THERE = "missing here"
UNKNOWN = "no manifest entry"


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


def read_manifest() -> dict[str, str]:
    """Hashes recorded at the last sync: {filename: sha256}."""
    if not MANIFEST.is_file():
        return {}
    out: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2:
            out[Path(parts[1]).name] = parts[0]
    return out


def classify(server_repo: Path) -> list[tuple[str, str, Path, Path]]:
    """Work out, per file, which side moved since the last sync."""
    src_dir = server_repo / PROVIDER_REL
    base = read_manifest()
    rows = []
    for name in EFFECT_FILES:
        src, dst = src_dir / name, EFFECTS_DIR / name
        if not src.is_file() and not dst.is_file():
            state = "missing on both sides"
        elif not src.is_file():
            state = NEW_HERE
        elif not dst.is_file():
            state = NEW_THERE
        else:
            s, d = sha256_of(src), sha256_of(dst)
            if s == d:
                state = IN_SYNC
            elif name not in base:
                state = UNKNOWN
            elif d == base[name]:
                state = THEIRS
            elif s == base[name]:
                state = OURS
            else:
                state = BOTH
        rows.append((name, state, src, dst))
    return rows


def write_manifest(server_repo: Path, direction: str) -> None:
    lines = [
        "# TuneThatHue effects manifest - written by tools/sync_effects.py",
        f"# synced: {datetime.now(timezone.utc).isoformat()}  ({direction})",
        f"# provider: {server_repo} @ {source_commit(server_repo)}",
    ]
    for name in EFFECT_FILES:
        f = EFFECTS_DIR / name
        if f.is_file():
            lines.append(f"{sha256_of(f)}  hue_fx/{name}")
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def status(server_repo: Path) -> int:
    rows = classify(server_repo)
    print(f"provider: {server_repo / PROVIDER_REL}")
    print(f"here    : {EFFECTS_DIR}\n")
    for name, state, _s, _d in rows:
        mark = "ok " if state == IN_SYNC else "-> "
        print(f"  {mark}{name:<20} {state}")
    theirs = [r for r in rows if r[1] in (THEIRS, NEW_THERE)]
    ours = [r for r in rows if r[1] in (OURS, NEW_HERE)]
    both = [r for r in rows if r[1] in (BOTH, UNKNOWN)]
    print()
    if both:
        print("  BOTH sides changed - resolve by hand, or force one direction:")
        print("    --diff   to see it,  --pull --force  or  --push --force")
    if theirs:
        print(f"  --pull would bring in {len(theirs)} file(s) from the provider")
    if ours:
        print(f"  --push would ship {len(ours)} file(s) to the provider")
    if not (theirs or ours or both):
        print("  everything is in sync")
    return 0


def diff(server_repo: Path) -> int:
    shown = 0
    for name, state, src, dst in classify(server_repo):
        if state == IN_SYNC or not (src.is_file() and dst.is_file()):
            continue
        if name.endswith(".json"):
            print(f"--- {name}: {state} (binary-ish, diff skipped)")
            shown += 1
            continue
        a = src.read_text(encoding="utf-8").splitlines(keepends=True)
        b = dst.read_text(encoding="utf-8").splitlines(keepends=True)
        print(f"\n===== {name}  ({state}) =====")
        sys.stdout.writelines(
            difflib.unified_diff(a, b, fromfile=f"provider/{name}", tofile=f"here/{name}", n=2)
        )
        shown += 1
    if not shown:
        print("no differences")
    return 0


def transfer(server_repo: Path, direction: str, force: bool) -> int:
    """Copy one way. Refuses when the other side also moved, unless forced."""
    src_dir = server_repo / PROVIDER_REL
    if not src_dir.is_dir():
        print(f"ERROR: provider dir not found: {src_dir}", file=sys.stderr)
        return 2
    EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    init_py = EFFECTS_DIR / "__init__.py"
    if not init_py.exists():
        init_py.write_text(
            '"""The Music Assistant hue_entertainment engine, kept byte-identical.\n\n'
            "Edit freely to develop effects, then ship it back with\n"
            'tools/sync_effects.py --push."""\n',
            encoding="utf-8",
        )

    rows = classify(server_repo)
    # Guard: pulling would destroy local work, pushing would destroy theirs.
    blocked = [
        (n, st)
        for n, st, _s, _d in rows
        if (direction == "pull" and st in (OURS, BOTH))
        or (direction == "push" and st in (THEIRS, BOTH))
    ]
    if blocked and not force:
        print("REFUSING: this would overwrite changes that are not in the manifest:\n")
        for n, st in blocked:
            print(f"  {n:<20} {st}")
        print("\nRun --diff to inspect, or repeat with --force once you are sure.")
        return 1

    moved = 0
    for name, state, src, dst in rows:
        a, b = (src, dst) if direction == "pull" else (dst, src)
        if not a.is_file():
            print(f"skip    {name}  (nothing to copy: {state})")
            continue
        if state == IN_SYNC:
            continue
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(a, b)
        print(f"{direction}ed  {name}")
        moved += 1

    write_manifest(server_repo, direction)
    print(f"\nOK  {moved} file(s) {direction}ed; manifest -> {MANIFEST}")
    if direction == "push" and moved:
        print("Remember: the provider is a git repo - review and commit there.")
    return 0


def check(server_repo: Path) -> int:
    rows = classify(server_repo)
    bad = 0
    for name, state, _s, _d in rows:
        if state == IN_SYNC:
            print(f"ok      {name}")
        else:
            print(f"DRIFT   {name}  ({state})")
            bad += 1
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server-repo", type=Path, default=DEFAULT_SERVER_REPO)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--pull", action="store_true", help="provider -> here")
    g.add_argument("--push", action="store_true", help="here -> provider")
    g.add_argument("--check", action="store_true", help="exit 1 if out of sync")
    g.add_argument("--diff", action="store_true", help="show the differences")
    p.add_argument("--force", action="store_true", help="copy even if the other side moved")
    args = p.parse_args()

    if args.check:
        return check(args.server_repo)
    if args.diff:
        return diff(args.server_repo)
    if args.pull:
        return transfer(args.server_repo, "pull", args.force)
    if args.push:
        return transfer(args.server_repo, "push", args.force)
    return status(args.server_repo)


if __name__ == "__main__":
    raise SystemExit(main())
