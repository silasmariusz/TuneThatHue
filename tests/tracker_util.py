"""Shared helper: run BeatTracker over in-memory PCM with the daemon's chunking."""

from __future__ import annotations

import numpy as np
from beat_tracker import BeatTracker

WARMUP_US = 5_000_000


def run_tracker(
    pcm: np.ndarray,
    sample_rate: int = 48000,
    chunk_frames: int = 1200,
    tracker: BeatTracker | None = None,
    anchor_us: int = 0,
) -> tuple[list, list[dict], BeatTracker]:
    """Feed int16 mono PCM; return (beats, events, tracker)."""
    tracker = tracker or BeatTracker(sample_rate, 1)
    events: list[dict] = []
    tracker.on_event = events.append
    beats: list = []
    samples_seen = 0
    raw = pcm.tobytes()
    chunk_bytes = chunk_frames * 2
    for off in range(0, len(raw) - 1, chunk_bytes):
        chunk = raw[off : off + chunk_bytes]
        ts = anchor_us + samples_seen * 1_000_000 // sample_rate
        samples_seen += len(chunk) // 2
        beats += tracker.process(chunk, ts)
    return beats, events, tracker


def post_warmup(beats: list) -> list[int]:
    """Emitted timestamps after the tracker warm-up window."""
    return [b.timestamp_us for b in beats if b.timestamp_us >= WARMUP_US]


def flips(events: list[dict]) -> int:
    """
    MUSICAL downbeat moves: consecutive committed "1" frames that are not a
    whole number of bars apart (within half a beat). Index renumbering across
    phase re-basing does not count - only a real shift of the "1" does.
    """
    seq = [
        (e["downbeat_frame"], e["period_frames"])
        for e in events
        if e.get("accepted") and e.get("downbeat_frame") is not None
    ]
    count = 0
    for (db1, _), (db2, period) in zip(seq, seq[1:]):
        bar = 4.0 * period
        delta = (db2 - db1) % bar
        if min(delta, bar - delta) > 0.5 * period:
            count += 1
    return count
