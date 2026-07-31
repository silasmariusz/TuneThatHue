#!/usr/bin/env python3
"""
Write the synthetic beat-tracking test set: WAV files + exact truth JSON.

Each case produces <name>.wav (16-bit mono) and <name>.truth.json with
{"bpm", "sample_rate", "beats_us", "downbeats_us"} - exact by construction
(the audio is rendered FROM the truth grid, see tools/synth.py).

Usage:
    python tools/make_clicks.py --out E:\\mp3\\_synthetic [--seconds 60]
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth  # noqa: E402


def write_wav(path: Path, pcm, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def write_case(
    out: Path,
    name: str,
    times: list[float],
    downs: list[bool],
    seconds: float,
    sample_rate: int,
    bpm: float | None,
    **render_kwargs,
) -> None:
    pcm = synth.render(times, downs, seconds, sample_rate, **render_kwargs)
    write_wav(out / f"{name}.wav", pcm, sample_rate)
    truth = {
        "bpm": bpm,
        "sample_rate": sample_rate,
        "beats_us": synth.to_us(times),
        "downbeats_us": synth.to_us([t for t, d in zip(times, downs) if d]),
    }
    (out / f"{name}.truth.json").write_text(json.dumps(truth), encoding="utf-8")
    print(f"  {name}.wav  ({len(times)} beats)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--sr", type=int, default=48000)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sec, sr = args.seconds, args.sr

    for bpm in (70, 85, 100, 118, 125, 128, 136, 150, 174, 185):
        times = synth.const_beat_times(bpm, sec)
        write_case(out, f"click_{bpm}", times, synth.default_downbeats(times), sec, sr, bpm)

    times = synth.const_beat_times(128, sec)
    downs = synth.default_downbeats(times)
    write_case(out, "click_128_noise", times, downs, sec, sr, 128, noise_db=-12.0)
    write_case(out, "kickbar_128", times, downs, sec, sr, 128, hats=True)
    write_case(out, "click_128_44k", times, downs, sec, 44100, 128)

    times = synth.ramp_beat_times(128, 131, sec)
    write_case(out, "ramp_128_131", times, synth.default_downbeats(times), sec, sr, None)

    # gap_136: only the outer bars are RENDERED, but the grid runs through the
    # silence - so the truth is the FULL grid (a coasting tracker's beats in the
    # gap are correct, not false positives).
    times, downs, total = synth.gap_beat_times(136, bars_before=16, bars_gap=8, bars_after=16)
    pcm = synth.render(times, downs, total, sr)
    write_wav(out / "gap_136.wav", pcm, sr)
    full = synth.const_beat_times(136, total)[: (16 + 8 + 16) * 4]
    full_downs = synth.default_downbeats(full)
    (out / "gap_136.truth.json").write_text(
        json.dumps(
            {
                "bpm": 136,
                "sample_rate": sr,
                "beats_us": synth.to_us(full),
                "downbeats_us": synth.to_us([t for t, d in zip(full, full_downs) if d]),
            }
        ),
        encoding="utf-8",
    )
    print(f"  gap_136.wav  ({len(times)} audible / {len(full)} grid beats)")

    times = synth.const_beat_times(123, sec)
    downs = synth.default_downbeats(times)
    pcm = synth.render_delay_trap(times, downs, sec, sr)
    write_wav(out / "delaytrap_123.wav", pcm, sr)
    (out / "delaytrap_123.truth.json").write_text(
        json.dumps(
            {
                "bpm": 123,
                "sample_rate": sr,
                "beats_us": synth.to_us(times),
                "downbeats_us": synth.to_us([t for t, d in zip(times, downs) if d]),
            }
        ),
        encoding="utf-8",
    )
    print(f"  delaytrap_123.wav  ({len(times)} beats)")

    pcm = synth.noise_only(30.0, sr)
    write_wav(out / "noise_only.wav", pcm, sr)
    (out / "noise_only.truth.json").write_text(
        json.dumps({"bpm": None, "sample_rate": sr, "beats_us": [], "downbeats_us": []}),
        encoding="utf-8",
    )
    print("  noise_only.wav  (0 beats)")


if __name__ == "__main__":
    main()
