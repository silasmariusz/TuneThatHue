"""No-beat fallback (ghost / breathe) and the silence idle drift."""

from __future__ import annotations

from types import SimpleNamespace

SEC = 1_000_000
PERIOD_US = int(60 * SEC / 128)


def make_analyzer(no_beat: str):
    from hue_fx.analyzer import HueAudioAnalyzer

    try:
        from hue_entertainment import LightChannel

        channels = [
            LightChannel(channel_id=0, name="a", position=(-1.0, 0.0, 0.0), service_id=""),
            LightChannel(channel_id=1, name="b", position=(1.0, 0.0, 0.0), service_id=""),
        ]
    except ImportError:
        channels = [
            SimpleNamespace(channel_id=0, name="a", position=(-1.0, 0.0, 0.0)),
            SimpleNamespace(channel_id=1, name="b", position=(1.0, 0.0, 0.0)),
        ]
    return HueAudioAnalyzer(channels, color_mode="smooth", no_beat=no_beat)


def feed(analyzer, start_us: int, seconds: float, beats: bool, audio: bool = True) -> int:
    """Push spectrum (and optionally beats) covering [start, start+seconds)."""
    us = start_us
    end = start_us + int(seconds * SEC)
    next_beat = start_us
    while us < end:
        if audio:
            analyzer.apply_spectrum([30000] * 17, us)
        if beats and us >= next_beat:
            analyzer.push_beats(
                [SimpleNamespace(timestamp_us=next_beat, is_downbeat=(next_beat // PERIOD_US) % 4 == 0)]
            )
            next_beat += PERIOD_US
        us += 50_000
    return end


def max_level(commands) -> float:
    return max((max(c.red, c.green, c.blue) for c in commands), default=0.0)


def render_levels(analyzer, start_us: int, seconds: float, audio: bool = True) -> list[float]:
    levels = []
    us = start_us
    end = start_us + int(seconds * SEC)
    while us < end:
        if audio:
            analyzer.apply_spectrum([30000] * 17, us)
        levels.append(max_level(analyzer.render(us)))
        us += 50_000
    return levels


def test_ghost_pulses_at_remembered_tempo() -> None:
    a = make_analyzer("ghost")
    t = feed(a, 0, 10.0, beats=True)
    a.clear_beat_schedule()  # the schedule dries up, music keeps playing
    a.push_beats([SimpleNamespace(timestamp_us=t - PERIOD_US, is_downbeat=False)])
    a._beats.clear()  # dry schedule but a remembered last beat
    levels = render_levels(a, t, 4.0)
    assert max(levels) > 0, "ghost went fully dark"
    # The envelope must actually pulse: clear swing between crest and trough.
    assert max(levels) - min(levels) > 0.1 * max(levels), f"no pulse: {min(levels)}-{max(levels)}"
    # And it must be dimmed against the on-beat look (channels are 16-bit).
    assert max(levels) < 65535 * 0.75


def test_breathe_swells_slowly() -> None:
    a = make_analyzer("breathe")
    t = feed(a, 0, 10.0, beats=True)
    a._beats.clear()
    levels = render_levels(a, t, 8.0)
    assert max(levels) > 0
    assert max(levels) - min(levels) > 0.1 * max(levels), "no swell"
    # A slow swell, not a per-beat flicker: neighbouring samples move gently.
    steps = [abs(b - a2) for a2, b in zip(levels, levels[1:])]
    assert max(steps) < 0.2 * max(levels), "breathe is flickering"


def test_onsets_mode_keeps_plain_fallback() -> None:
    a = make_analyzer("onsets")
    t = feed(a, 0, 10.0, beats=True)
    a._beats.clear()
    levels = render_levels(a, t, 2.0)
    assert max(levels) > 0


def test_never_had_beats_keeps_original_look() -> None:
    """A stream with no beat analysis at all must not start breathing."""
    a = make_analyzer("ghost")
    feed(a, 0, 5.0, beats=False)
    levels = render_levels(a, int(5.0 * SEC), 3.0)
    assert max(levels) > 0
    # The plain onset walk holds steady brightness (no breathe envelope).
    assert max(levels) - min(levels) < 0.35 * max(levels)


def test_silence_goes_idle_and_stays_alive() -> None:
    a = make_analyzer("ghost")
    t = feed(a, 0, 10.0, beats=True)
    # No more spectrum: after the idle delay the room dims but keeps moving.
    active_level = max(render_levels(a, t, 1.0))
    idle_start = t + int(5.0 * SEC)
    levels = render_levels(a, idle_start, 8.0, audio=False)
    assert max(levels) > 0, "idle went fully dark"
    assert max(levels) < active_level * 0.6, "idle is not dimmed"
    assert max(levels) - min(levels) > 2, "idle does not breathe"


def test_audio_return_wakes_from_idle() -> None:
    a = make_analyzer("ghost")
    t = feed(a, 0, 10.0, beats=True)
    idle_levels = render_levels(a, t + int(5 * SEC), 2.0, audio=False)
    t2 = t + int(8 * SEC)
    t3 = feed(a, t2, 5.0, beats=True)
    live_levels = render_levels(a, t3 - int(1 * SEC), 1.0)
    assert max(live_levels) > max(idle_levels), "did not wake from idle"
