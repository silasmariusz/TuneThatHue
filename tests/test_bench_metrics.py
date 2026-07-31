"""Unit tests for the local mir_eval-style metric implementations."""

from __future__ import annotations

import beat_metrics as bm

SEC = 1_000_000


def grid(bpm: float, n: int, offset_us: int = 0) -> list[int]:
    period = int(60 * SEC / bpm)
    return [offset_us + k * period for k in range(n)]


def test_f_measure_perfect() -> None:
    ref = grid(120, 100)
    assert bm.f_measure(ref, ref)["f"] == 1.0


def test_f_measure_tolerates_small_offset() -> None:
    ref = grid(120, 100)
    est = [t + 30_000 for t in ref]  # 30 ms early/late is within +/-70 ms
    assert bm.f_measure(est, ref)["f"] == 1.0


def test_f_measure_penalises_duplicates() -> None:
    ref = grid(120, 100)
    est = sorted(ref + [t + 5_000 for t in ref])  # every beat twice
    scores = bm.f_measure(est, ref)
    assert scores["recall"] == 1.0
    assert scores["precision"] <= 0.5 + 1e-9


def test_duplicate_and_skip_rates() -> None:
    period = 500_000
    est = [0, period, period + 10_000, 2 * period, 4 * period]  # one dup, one 2x gap
    stats = bm.ibi_stats(est)
    assert stats["duplicate_rate"] > 0
    assert stats["skip_rate"] > 0

    clean = grid(120, 50)
    stats = bm.ibi_stats(clean)
    assert stats["duplicate_rate"] == 0
    assert stats["skip_rate"] == 0


def test_continuity_full_on_perfect() -> None:
    ref = grid(128, 200)
    scores = bm.continuity(ref, ref)
    assert scores["cmlt"] > 0.99
    assert scores["amlt"] > 0.99


def test_continuity_offbeat_counts_for_aml_only() -> None:
    ref = grid(128, 200)
    period = ref[1] - ref[0]
    offbeat = [t + period // 2 for t in ref[:-1]]
    scores = bm.continuity(offbeat, ref)
    assert scores["cmlt"] < 0.2
    assert scores["amlt"] > 0.9


def test_tempo_verdicts() -> None:
    assert bm.tempo_verdict(128.5, 128) == "ok"
    assert bm.tempo_verdict(256, 128) == "double"
    assert bm.tempo_verdict(64, 128) == "half"
    assert bm.tempo_verdict(100, 128) == "wrong"
