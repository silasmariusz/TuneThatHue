"""Behavioural tests for the live beat tracker (synthetic PCM, exact truth)."""

from __future__ import annotations

import numpy as np
import pytest
import synth
import tracker_util as tu

SEC = 1_000_000


def steady(bpm: float, seconds: float = 60.0, **kw) -> np.ndarray:
    times = synth.const_beat_times(bpm, seconds)
    return synth.render(times, synth.default_downbeats(times), seconds, **kw)


def test_no_reemission_steady() -> None:
    """I1: every emitted beat is distinct - no duplicate re-emission (S1)."""
    beats, _, _ = tu.run_tracker(steady(128))
    est = tu.post_warmup(beats)
    assert len(est) > 40
    ibis = [b - a for a, b in zip(est, est[1:])]
    assert min(ibis) > 0, "timestamps must be strictly increasing"
    median = sorted(ibis)[len(ibis) // 2]
    assert min(ibis) > 0.6 * median, f"duplicate-ish IBI found: min {min(ibis)} vs median {median}"
    expected = 55 * 128 / 60  # beats in the scored 55 s window
    assert len(est) < expected * 1.15, f"{len(est)} beats emitted, expected about {expected:.0f}"


def test_continuity_across_relatches() -> None:
    """I2: re-estimates refine the grid without tearing it (no IBI outliers)."""
    times = synth.ramp_beat_times(127.6, 128.4, 60.0)
    pcm = synth.render(times, synth.default_downbeats(times), 60.0)
    beats, _, _ = tu.run_tracker(pcm)
    est = tu.post_warmup(beats)
    assert len(est) > 40
    nominal = 60 * SEC / 128
    for a, b in zip(est, est[1:]):
        assert 0.8 * nominal < b - a < 1.2 * nominal, f"IBI tear: {b - a} vs nominal {nominal:.0f}"


@pytest.mark.parametrize("bpm", [90, 110, 125, 136, 150, 174, 185])
def test_tempo_accuracy(bpm: float) -> None:
    _, events, tracker = tu.run_tracker(steady(bpm))
    assert tracker.bpm == pytest.approx(bpm, rel=0.04)
    accepted = [e["bpm"] for e in events if e.get("accepted")]
    median = sorted(accepted)[len(accepted) // 2]
    assert median == pytest.approx(bpm, rel=0.04)


def test_delay_trap_does_not_pull_tempo() -> None:
    """Dotted-eighth delay pings (3/4-beat spacing) must not read as 4/3 tempo."""
    seconds = 90.0
    times = synth.const_beat_times(123, seconds)
    pcm = synth.render_delay_trap(times, synth.default_downbeats(times), seconds)
    _, events, tracker = tu.run_tracker(pcm)
    accepted = [e["bpm"] for e in events if e.get("accepted")]
    assert accepted, "never locked on the delay-trap signal"
    median = sorted(accepted)[len(accepted) // 2]
    assert median == pytest.approx(123, rel=0.04), f"median bpm {median}"
    wrong = [b for b in accepted if abs(b - 164) < 8 or abs(b - 137) < 5]
    assert len(wrong) / len(accepted) < 0.1, f"{len(wrong)}/{len(accepted)} estimates on a trap tempo"


def test_vinyl_wow_follows_the_float() -> None:
    """A floating vinyl rip (sinusoidal +/-1.5% wow) must stay on the beat."""
    times = synth.wow_beat_times(100, 90.0, depth=0.015, rate_hz=0.5)
    pcm = synth.render(times, synth.default_downbeats(times), 90.0)
    beats, _, tracker = tu.run_tracker(pcm)
    assert tracker.bpm == pytest.approx(100, rel=0.05)
    est = tu.post_warmup(beats)
    truth = [int(t * SEC) for t in times if t * SEC >= tu.WARMUP_US]
    import beat_metrics as bm

    scores = bm.f_measure(est, truth)
    assert scores["f"] > 0.9, f"wow tracking degraded: {scores}"


def test_tempo_change_in_stream() -> None:
    """A real tempo change mid-stream (mixed playlist) must re-latch quickly."""
    a = synth.const_beat_times(124, 60.0)
    b = [60.0 + t for t in synth.const_beat_times(90, 60.0)]
    pcm_a = synth.render(a, synth.default_downbeats(a), 60.0)
    pcm_b = synth.render([t - 60.0 for t in b], synth.default_downbeats(b), 60.0)
    import numpy as np

    beats, events, tracker = tu.run_tracker(np.concatenate([pcm_a, pcm_b]))
    assert tracker.bpm == pytest.approx(90, rel=0.04), f"end bpm {tracker.bpm}"
    # The switch must land within 20 s of the change (guards must not wedge it).
    late = [e for e in events if e.get("accepted") and e["frame"] / 93.75 > 80.0]
    assert late, "no accepted estimates after the change"
    for e in late:
        assert e["bpm"] == pytest.approx(90, rel=0.05), f"still wrong at t={e['frame'] / 93.75:.0f}s"


def test_downbeat_stability() -> None:
    """I3: with a clean accent the committed downbeat never flips."""
    _, events, _ = tu.run_tracker(steady(128))
    assert tu.flips(events) == 0, f"bar offset flipped {tu.flips(events)}x on clean input"


def test_downbeat_hysteresis() -> None:
    """I3: a short accent anomaly (2 bars) must not move the committed downbeat."""
    seconds = 60.0
    times = synth.const_beat_times(128, seconds)
    downs = synth.default_downbeats(times)
    # Bars 12-13: accent shifted to beat 2 (a fill), then back to normal.
    for k in range(len(times)):
        bar, pos = k // 4, k % 4
        if bar in (12, 13):
            downs[k] = pos == 2
    pcm = synth.render(times, downs, seconds)
    _, events, _ = tu.run_tracker(pcm)
    assert tu.flips(events) == 0, f"anomaly flipped the committed downbeat {tu.flips(events)}x"


def test_downbeat_marked_every_fourth_beat() -> None:
    beats, _, _ = tu.run_tracker(steady(128))
    tail = [b for b in beats if b.timestamp_us >= tu.WARMUP_US]
    down_idx = [i for i, b in enumerate(tail) if b.is_downbeat]
    assert down_idx, "no downbeats emitted"
    gaps = {b - a for a, b in zip(down_idx, down_idx[1:])}
    assert gaps == {4}, f"downbeat spacing not 4: {sorted(gaps)}"


def test_coasting_through_gap() -> None:
    """I4: an 8-bar breakdown must not silence or de-phase the grid."""
    times, downs, total = synth.gap_beat_times(136, bars_before=16, bars_gap=8, bars_after=16)
    pcm = synth.render(times, downs, total)
    beats, _, _ = tu.run_tracker(pcm)
    est = tu.post_warmup(beats)
    assert len(est) > 60
    period = 60 * SEC / 136
    for a, b in zip(est, est[1:]):
        assert b - a < 1.5 * period, f"emission gap {b - a} > 1.5 periods (no coasting)"
    # Post-gap: emitted beats must still sit on the ORIGINAL grid (phase held).
    grid_start = int(0.1 * SEC)
    after_gap_us = grid_start + int((16 + 8) * 4 * period)
    tail = [t for t in est if t >= after_gap_us + int(4 * period)]
    assert tail, "no beats after the gap"
    for t in tail[:16]:
        phase_err = (t - grid_start) % period
        phase_err = min(phase_err, period - phase_err)
        assert phase_err < 70_000, f"post-gap phase error {phase_err / 1000:.1f} ms"


def test_coast_hard_stop() -> None:
    """I4: coasting ends after _COAST_BARS bars of silence."""
    import beat_tracker as bt

    seconds = 20.0
    pcm_music = steady(136, seconds)
    silence = np.zeros(int(150.0 * 48000), dtype=np.int16)
    beats, _, _ = tu.run_tracker(np.concatenate([pcm_music, silence]))
    assert beats, "no beats at all"
    last = max(b.timestamp_us for b in beats)
    bar_us = 4 * 60 * SEC / 136
    # The ACF history is 8 s long, so the tracker legitimately stays locked for
    # up to that long after the music stops; the coast window starts there.
    deadline = (seconds + 8.0) * SEC + (bt._COAST_BARS + 2) * bar_us
    assert last < deadline, f"still emitting {last / SEC:.1f}s (deadline {deadline / SEC:.1f}s)"


def test_no_false_lock_on_noise() -> None:
    beats, _, _ = tu.run_tracker(synth.noise_only(30.0))
    assert beats == []


def test_reset_clears_frontier() -> None:
    """After reset() a re-anchored stream must produce beats again."""
    beats1, _, tracker = tu.run_tracker(steady(128, 20.0))
    assert beats1
    tracker.reset()
    tracker.on_event = None
    beats2, _, _ = tu.run_tracker(steady(128, 20.0), tracker=tracker, anchor_us=0)
    assert beats2, "no beats after reset + re-anchor"
