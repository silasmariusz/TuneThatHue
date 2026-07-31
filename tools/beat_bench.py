#!/usr/bin/env python3
"""
Offline benchmark for the live beat tracker + the beat_in_bar counter.

Feeds audio files through ``BeatTracker`` with EXACTLY the daemon's chunking and
timestamp math (see tth_phase2.on_chunk), logs every emitted beat and estimator
decision to JSONL, and scores the run:

  - F-measure +/-70 ms and CML/AML continuity vs a truth grid
    (``<name>.truth.json`` written by tools/make_clicks.py), when present;
  - tempo accuracy vs the BPM encoded in trance corpus filenames
    (``"08A, 136 - Artist - Title.mp3"``), with half/double octave flags;
  - duplicate rate (IBI < 0.5x median) - the re-emission detector;
  - skip rate (IBI > 1.5x median) - the dropout detector;
  - lock/coast coverage and bar-offset flip count.

Counter-integrity replay (``--counter-replay``): pushes a beat schedule through
the real analyzer + StructureDetector the way the daemon does (batched ~1.2 s
ahead of "now") and samples ``beat_in_bar`` every 20 ms, verifying a strict
1,2,3,4 cycle. ``--counter-replay perfect:BPM:SECONDS`` uses an exact grid (a
failure there implicates the counter, not the tracker); pass a bench JSONL path
to replay a real tracker run instead.

Usage:
    python tools/beat_bench.py E:\\mp3\\_synthetic --out runs/base-synth
    python tools/beat_bench.py "E:\\mp3\\B_2022_Classic_Trance" --limit-s 180 --out runs/base-trance
    python tools/beat_bench.py --counter-replay perfect:128:120
    python tools/beat_bench.py --counter-replay runs/base-trance/track.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "python"))

import beat_metrics  # noqa: E402
from beat_tracker import BeatTracker  # noqa: E402

AUDIO_EXT = {".mp3", ".flac", ".wav", ".m4a", ".ogg"}
FILENAME_BPM = re.compile(r"^\d{2}[AB], (\d{2,3}) - ")
WARMUP_US = 5_000_000  # ignore the first seconds when scoring (tracker needs history)


# -- decode + feed -----------------------------------------------------------


def decode_pcm(path: Path) -> tuple[bytes, int, int]:
    """Return (interleaved int16 PCM, sample_rate, channels) for any audio file."""
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() == 2:
                return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    sr, ch = 48000, 2
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", str(ch), "-ar", str(sr), "-"],
        capture_output=True,
        check=True,
    )
    return proc.stdout, sr, ch


def run_tracker(
    pcm: bytes, sample_rate: int, channels: int, chunk_frames: int, limit_s: float | None
) -> dict:
    """Feed PCM through BeatTracker with the daemon's chunk/timestamp math."""
    tracker = BeatTracker(sample_rate, channels)
    events: list[dict] = []
    tracker.on_event = events.append

    beats: list[dict] = []
    locks: list[dict] = []
    frame_bytes = channels * 2
    chunk_bytes = chunk_frames * frame_bytes
    anchor_us = 0
    samples_seen = 0
    last_locked = False
    limit_samples = int(limit_s * sample_rate) if limit_s else None

    for off in range(0, len(pcm) - frame_bytes + 1, chunk_bytes):
        if limit_samples is not None and samples_seen >= limit_samples:
            break
        chunk = pcm[off : off + chunk_bytes]
        ts = anchor_us + samples_seen * 1_000_000 // sample_rate
        samples_seen += len(chunk) // frame_bytes
        for beat in tracker.process(chunk, ts):
            beats.append({"us": beat.timestamp_us, "down": bool(beat.is_downbeat)})
        if tracker.locked != last_locked:
            last_locked = tracker.locked
            locks.append({"us": ts, "locked": last_locked})

    return {
        "beats": beats,
        "events": events,
        "locks": locks,
        "duration_us": samples_seen * 1_000_000 // sample_rate,
        "final_bpm": tracker.bpm,
    }


# -- scoring -----------------------------------------------------------------


def truth_for(path: Path) -> dict | None:
    """Load ``<stem>.truth.json`` next to the file, if present."""
    candidate = path.parent / (path.stem + ".truth.json")
    if candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def filename_bpm(path: Path) -> float | None:
    """BPM encoded in the trance-corpus filename, if any."""
    m = FILENAME_BPM.match(path.name)
    return float(m.group(1)) if m else None


def locked_coverage(locks: list[dict], duration_us: int) -> float:
    """Fraction of (post-warmup) time spent locked."""
    if duration_us <= WARMUP_US:
        return 0.0
    total = 0
    state = False
    prev = WARMUP_US
    for tr in locks:
        t = max(tr["us"], WARMUP_US)
        if state:
            total += max(0, t - prev)
        prev = t
        state = tr["locked"]
    if state:
        total += max(0, duration_us - prev)
    return total / (duration_us - WARMUP_US)


def bar_offset_flips(events: list[dict]) -> int:
    """
    Musical downbeat moves: committed "1" frames not a whole number of bars
    apart (within half a beat). Falls back to the legacy bar_offset field for
    runs recorded before the tracker exported downbeat_frame.
    """
    seq = [
        (e["downbeat_frame"], e["period_frames"])
        for e in events
        if e.get("accepted") and e.get("downbeat_frame") is not None
    ]
    if seq:
        count = 0
        for (db1, _), (db2, period) in zip(seq, seq[1:]):
            bar = 4.0 * period
            delta = (db2 - db1) % bar
            if min(delta, bar - delta) > 0.5 * period:
                count += 1
        return count
    legacy = [e["bar_offset"] for e in events if e.get("accepted") and "bar_offset" in e]
    return sum(1 for a, b in zip(legacy, legacy[1:]) if a != b)


def score_track(path: Path, run: dict, limit_s: float | None) -> dict:
    """Compute the per-track metric row."""
    est = [b["us"] for b in run["beats"] if b["us"] >= WARMUP_US]
    est_down = [b["us"] for b in run["beats"] if b["down"] and b["us"] >= WARMUP_US]
    row: dict = {
        "file": path.name,
        "n_beats": len(est),
        "final_bpm": round(run["final_bpm"], 2),
        "lock_coverage": round(locked_coverage(run["locks"], run["duration_us"]), 3),
        "bar_flips": bar_offset_flips(run["events"]),
    }
    row.update({k: round(v, 4) if isinstance(v, float) else v
                for k, v in beat_metrics.ibi_stats(est).items()})

    truth = truth_for(path)
    truth_bpm = (truth or {}).get("bpm") or filename_bpm(path)
    if truth_bpm:
        bpms = [e["bpm"] for e in run["events"] if e.get("accepted") and e.get("bpm")]
        bpm_est = sorted(bpms)[len(bpms) // 2] if bpms else run["final_bpm"]
        row["truth_bpm"] = truth_bpm
        row["bpm_est"] = round(bpm_est, 2)
        row["tempo"] = beat_metrics.tempo_verdict(bpm_est, truth_bpm)
    if truth and truth["beats_us"]:
        limit_us = int(limit_s * 1_000_000) if limit_s else None
        ref = [t for t in truth["beats_us"] if t >= WARMUP_US and (not limit_us or t < limit_us)]
        ref_down = [
            t for t in truth["downbeats_us"] if t >= WARMUP_US and (not limit_us or t < limit_us)
        ]
        row.update({f"beat_{k}": round(v, 4) for k, v in beat_metrics.f_measure(est, ref).items()})
        row.update({k: round(v, 4) for k, v in beat_metrics.continuity(est, ref).items()})
        if ref_down:
            row["down_f"] = round(beat_metrics.f_measure(est_down, ref_down)["f"], 4)
    elif truth is not None and not truth["beats_us"]:
        row["false_beats"] = len(est)  # noise_only: anything emitted is wrong
    return row


# -- counter replay ----------------------------------------------------------


def _load_engine():
    """Import the real analyzer with the daemon's path wiring (pystub fallback)."""
    sys.path.insert(0, str(ROOT / "effects"))
    try:
        import hue_entertainment  # noqa: F401
    except ModuleNotFoundError:
        sys.path.insert(0, str(ROOT / "pystub"))
    from hue_fx.analyzer import HueAudioAnalyzer  # noqa: PLC0415

    try:
        from hue_entertainment import LightChannel  # noqa: PLC0415
    except ImportError:
        LightChannel = None  # type: ignore[assignment]
    return HueAudioAnalyzer, LightChannel


def counter_replay(beats: list[tuple[int, bool]], sample_ms: int = 20) -> dict:
    """
    Push a beat schedule through the analyzer as the daemon would and check the
    1/4..4/4 counter for skips and double-counts.

    Returns {"transitions", "bad_transitions", "samples", "bad_detail"}.
    """
    HueAudioAnalyzer, LightChannel = _load_engine()
    mk = LightChannel or SimpleNamespace
    channels = [
        mk(channel_id=0, name="left", position=(-1.0, 0.0, 0.0), service_id=""),
        mk(channel_id=1, name="right", position=(1.0, 0.0, 0.0), service_id=""),
    ]
    analyzer = HueAudioAnalyzer(channels)
    structure = analyzer._structure  # noqa: SLF001 - the deliberate probe point

    lookahead_us = 1_200_000
    step_us = sample_ms * 1000
    start = beats[0][0]
    end = beats[-1][0]
    pushed = 0
    seq: list[tuple[int, int]] = []  # (now_us, beat_in_bar)
    now = start - 500_000
    while now <= end:
        while pushed < len(beats) and beats[pushed][0] <= now + lookahead_us:
            ts, down = beats[pushed]
            analyzer.push_beats([SimpleNamespace(timestamp_us=ts, is_downbeat=down)])
            pushed += 1
        analyzer.render(now)  # advances pruning like the daemon's render loop
        seq.append((now, structure.beat_in_bar(now)))
        now += step_us

    transitions = 0
    bad: list[dict] = []
    prev_bib = None
    for ts, bib in seq:
        if prev_bib is None:
            prev_bib = bib
            continue
        if bib != prev_bib:
            transitions += 1
            if bib != (prev_bib + 1) % 4:
                bad.append({"us": ts, "from": prev_bib, "to": bib})
            prev_bib = bib
    return {
        "samples": len(seq),
        "transitions": transitions,
        "bad_transitions": len(bad),
        "bad_detail": bad[:20],
    }


def perfect_grid(bpm: float, seconds: float) -> list[tuple[int, bool]]:
    """Exact beat schedule: (timestamp_us, is_downbeat) at a constant tempo."""
    period_us = 60_000_000 / bpm
    n = int(seconds * bpm / 60)
    return [(int(100_000 + k * period_us), k % 4 == 0) for k in range(n)]


def beats_from_jsonl(path: Path) -> list[tuple[int, bool]]:
    """Load the emitted beats from a bench JSONL run."""
    out: list[tuple[int, bool]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("t") == "beat":
            out.append((rec["us"], rec["down"]))
    return out


# -- CLI ---------------------------------------------------------------------


def collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files += sorted(f for f in path.iterdir() if f.suffix.lower() in AUDIO_EXT)
        elif path.is_file():
            files.append(path)
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--chunk-frames", type=int, default=1200)
    ap.add_argument("--limit-s", type=float)
    ap.add_argument("--counter-replay", metavar="perfect:BPM:SEC|RUN.jsonl")
    args = ap.parse_args()

    if args.counter_replay:
        if args.counter_replay.startswith("perfect:"):
            _, bpm, sec = args.counter_replay.split(":")
            beats = perfect_grid(float(bpm), float(sec))
            label = f"perfect {bpm} BPM / {sec}s"
        else:
            beats = beats_from_jsonl(Path(args.counter_replay))
            label = args.counter_replay
        result = counter_replay(beats)
        print(f"counter replay [{label}]: {json.dumps(result, indent=2)}")
        sys.exit(1 if result["bad_transitions"] else 0)

    files = collect_files(args.paths)
    if not files:
        ap.error("no audio files found")
    out_dir = Path(args.out) if args.out else Path("runs") / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for f in files:
        try:
            pcm, sr, ch = decode_pcm(f)
        except subprocess.CalledProcessError as err:
            print(f"!! decode failed: {f.name}: {err.stderr.decode(errors='replace')[:200]}")
            continue
        run = run_tracker(pcm, sr, ch, args.chunk_frames, args.limit_s)
        with (out_dir / (f.stem + ".jsonl")).open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": "meta", "file": f.name, "sr": sr, "ch": ch,
                                 "chunk_frames": args.chunk_frames}) + "\n")
            for b in run["beats"]:
                fh.write(json.dumps({"t": "beat", **b}) + "\n")
            for e in run["events"]:
                fh.write(json.dumps({"t": "est", **e}) + "\n")
            for tr in run["locks"]:
                fh.write(json.dumps({"t": "lock", **tr}) + "\n")
        row = score_track(f, run, args.limit_s)
        rows.append(row)
        brief = {k: row[k] for k in
                 ("n_beats", "final_bpm", "duplicate_rate", "skip_rate", "lock_coverage",
                  "bar_flips", "tempo", "beat_f", "down_f", "false_beats") if k in row}
        print(f"{f.name}: {brief}")

    keys: list[str] = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)

    def _avg(key: str) -> float | None:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 4) if vals else None

    print("\n== aggregate ==")
    for key in ("duplicate_rate", "skip_rate", "lock_coverage", "beat_f", "cmlt", "amlt", "down_f"):
        avg = _avg(key)
        if avg is not None:
            print(f"  {key}: {avg}")
    tempos = [r.get("tempo") for r in rows if r.get("tempo")]
    if tempos:
        ok = sum(1 for t in tempos if t == "ok")
        print(f"  tempo ok: {ok}/{len(tempos)}  ({sorted(set(tempos))})")
    flips = sum(r.get("bar_flips", 0) for r in rows)
    print(f"  bar_offset flips total: {flips}")
    print(f"\nwritten: {out_dir}\\summary.csv")


if __name__ == "__main__":
    main()
