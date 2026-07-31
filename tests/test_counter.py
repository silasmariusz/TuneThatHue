"""The 1/4..4/4 counter must track the actual beat schedule, not an EMA guess."""

from __future__ import annotations

from types import SimpleNamespace

import synth
import tracker_util as tu
from beat_bench import counter_replay, perfect_grid

SEC = 1_000_000


def cycle_errors(seq: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """Transitions that are not +1 mod 4: (us, from, to)."""
    bad = []
    prev = None
    for ts, bib in seq:
        if prev is not None and bib != prev and bib != (prev + 1) % 4:
            bad.append((ts, prev, bib))
        prev = bib
    return bad


def test_perfect_grid_counts_1234() -> None:
    """A perfect schedule must produce a strict 1,2,3,4 cycle - the layer-B/C gate."""
    result = counter_replay(perfect_grid(128, 180))
    assert result["bad_transitions"] == 0, result["bad_detail"]
    # Sanity: the counter actually moves (about 2 transitions per second at 128 BPM).
    assert result["transitions"] > 300


def test_perfect_grid_counts_1234_slow_and_fast() -> None:
    for bpm in (85, 140):
        result = counter_replay(perfect_grid(bpm, 120))
        assert result["bad_transitions"] == 0, (bpm, result["bad_detail"])


def test_counter_survives_schedule_dry() -> None:
    """When pushes stop, the counter extrapolates forward - never jumps back."""
    from hue_fx.structure import StructureDetector

    det = StructureDetector()
    period_us = int(60 * SEC / 128)
    beats = [(100_000 + k * period_us, k % 4 == 0) for k in range(64)]  # 30 s worth
    pushed = 0
    seq: list[tuple[int, int]] = []
    now = 0
    end = beats[-1][0] + 8 * period_us  # sample 2 bars past the last known beat
    while now <= end:
        while pushed < len(beats) and beats[pushed][0] <= now + 1_200_000:
            ts, down = beats[pushed]
            det.note_beat(ts, down)
            pushed += 1
        seq.append((now, det.beat_in_bar(now)))
        now += 20_000
    assert cycle_errors(seq) == []


def test_bar_phase_monotonic_within_bar() -> None:
    from hue_fx.structure import StructureDetector

    det = StructureDetector()
    period_us = int(60 * SEC / 128)
    beats = [(100_000 + k * period_us, k % 4 == 0) for k in range(128)]
    pushed = 0
    now = 100_000
    prev_phase = None
    wraps = 0
    end = beats[-1][0] - period_us
    while now <= end:
        while pushed < len(beats) and beats[pushed][0] <= now + 1_200_000:
            ts, down = beats[pushed]
            det.note_beat(ts, down)
            pushed += 1
        phase = det.bar_phase(now)
        if prev_phase is not None:
            if phase < prev_phase - 1e-6:
                wraps += 1
                assert prev_phase > 0.8, f"bar_phase dropped mid-bar: {prev_phase} -> {phase}"
                assert phase < 0.2, f"bar_phase wrapped to {phase}, expected near 0"
        prev_phase = phase
        now += 20_000
    assert wraps >= 20, f"only {wraps} bar wraps seen - bar_phase looks stuck"


def test_counter_with_tracker_output() -> None:
    """E2E: live tracker output through the counter - cycling stays clean after lock."""
    times = synth.const_beat_times(128, 60.0)
    pcm = synth.render(times, synth.default_downbeats(times), 60.0)
    beats, _, _ = tu.run_tracker(pcm)
    schedule = [
        (b.timestamp_us, bool(b.is_downbeat)) for b in beats if b.timestamp_us >= tu.WARMUP_US
    ]
    assert len(schedule) > 40
    result = counter_replay(schedule)
    good = result["transitions"] - result["bad_transitions"]
    assert result["transitions"] > 0
    ratio = good / result["transitions"]
    assert ratio >= 0.99, f"only {ratio:.3f} clean counter transitions: {result['bad_detail']}"


def test_counter_via_analyzer_object_shape() -> None:
    """push_beats accepts plain objects (BeatTiming is TYPE_CHECKING-only)."""
    from hue_fx.analyzer import HueAudioAnalyzer

    try:
        from hue_entertainment import LightChannel

        channels = [LightChannel(channel_id=0, name="a", position=(-1.0, 0.0, 0.0), service_id="")]
    except ImportError:
        channels = [SimpleNamespace(channel_id=0, name="a", position=(-1.0, 0.0, 0.0))]
    analyzer = HueAudioAnalyzer(channels)
    analyzer.push_beats([SimpleNamespace(timestamp_us=1_000_000, is_downbeat=True)])
    analyzer.render(1_100_000)
