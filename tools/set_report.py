#!/usr/bin/env python3
"""
Time-resolved report over beat_bench JSONL runs: WHERE does the beat get lost.

For each track, reports:
  - emission gaps: spans longer than GAP_PERIODS beat periods with no emitted
    beat (this is what "the lights lost the beat" looks like);
  - tempo timeline: the accepted-estimate BPM, compressed to change points;
  - lock timeline: locked spans vs dead air.

Usage:
    python tools/set_report.py runs\\djsets-long [pattern]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

GAP_PERIODS = 4.0  # a hole this many beats long counts as "lost the beat"


def fmt(us: float) -> str:
    s = us / 1_000_000
    return f"{int(s // 60):3d}:{s % 60:04.1f}"


def report(jsonl: Path) -> dict:
    beats: list[int] = []
    ests: list[dict] = []
    locks: list[dict] = []
    for line in jsonl.open(encoding="utf-8"):
        rec = json.loads(line)
        if rec["t"] == "beat":
            beats.append(rec["us"])
        elif rec["t"] == "est":
            ests.append(rec)
        elif rec["t"] == "lock":
            locks.append(rec)

    acc = [e for e in ests if e.get("accepted")]
    out: dict = {"file": jsonl.stem, "beats": len(beats), "accepted": len(acc)}

    # Median period from emitted beats (robust enough for gap detection).
    ibis = sorted(b - a for a, b in zip(beats, beats[1:]))
    if not ibis:
        out["gaps"] = []
        out["tempo_segments"] = []
        return out
    median_ibi = ibis[len(ibis) // 2]

    gaps = []
    for a, b in zip(beats, beats[1:]):
        if b - a > GAP_PERIODS * median_ibi:
            gaps.append((a, b))
    out["gaps"] = gaps
    out["median_bpm"] = 60_000_000 / median_ibi

    # Compress the accepted-estimate BPM into segments (change > 2%).
    segments: list[tuple[float, float, float]] = []  # (start_us, end_us, bpm)
    for e in acc:
        us = e["frame"] / 93.75 * 1_000_000
        bpm = e["bpm"]
        if segments and abs(bpm - segments[-1][2]) / segments[-1][2] < 0.02:
            segments[-1] = (segments[-1][0], us, segments[-1][2])
        else:
            segments.append((us, us, bpm))
    out["tempo_segments"] = segments
    return out


def main() -> None:
    run_dir = Path(sys.argv[1])
    pattern = sys.argv[2] if len(sys.argv) > 2 else "*"
    total_gap_s = 0.0
    for jsonl in sorted(run_dir.glob(pattern + ".jsonl")):
        r = report(jsonl)
        print(f"\n== {r['file']}")
        if not r.get("tempo_segments"):
            print("   (no beats at all)")
            continue
        print(f"   beats={r['beats']} median_bpm={r['median_bpm']:.1f}")
        segs = [s for s in r["tempo_segments"] if s[1] > s[0]]
        for start, end, bpm in segs:
            print(f"   tempo {bpm:6.1f}  {fmt(start)} - {fmt(end)}")
        if r["gaps"]:
            print(f"   LOST THE BEAT {len(r['gaps'])}x:")
            for a, b in r["gaps"]:
                dur = (b - a) / 1_000_000
                total_gap_s += dur
                print(f"     {fmt(a)} -> {fmt(b)}  ({dur:5.1f} s dark)")
        else:
            print("   no emission gaps")
    print(f"\ntotal dark time across tracks: {total_gap_s:.0f} s")


if __name__ == "__main__":
    main()
