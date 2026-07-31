"""
Synthetic test signals with exact beat-grid ground truth.

Every generator returns beat times in seconds, computed first, and the audio is
rendered FROM those times - so the truth is exact by construction, not measured.
Kicks are what the tracker listens for (bass-band phase/downbeat logic), so the
click is a decaying low sine, with the downbeat accented.

Shared between tools/make_clicks.py (writes WAV + truth JSON) and tests/ (feeds
the tracker in memory). numpy only.
"""

from __future__ import annotations

import numpy as np

KICK_HZ = 60.0
KICK_DECAY_S = 0.050
KICK_AMP = 0.45
DOWNBEAT_GAIN = 2.0  # +6 dB on the "1"
HAT_HZ = 8000.0
HAT_LEN_S = 0.020
HAT_AMP = 0.30


def const_beat_times(bpm: float, seconds: float, offset: float = 0.1) -> list[float]:
    """Beat times for a constant tempo, first beat at ``offset``."""
    period = 60.0 / bpm
    out: list[float] = []
    t = offset
    while t < seconds:
        out.append(t)
        t += period
    return out


def ramp_beat_times(bpm0: float, bpm1: float, seconds: float, offset: float = 0.1) -> list[float]:
    """Beat times for a tempo ramping linearly in time from bpm0 to bpm1."""
    out: list[float] = []
    t = offset
    while t < seconds:
        out.append(t)
        bpm = bpm0 + (bpm1 - bpm0) * (t / seconds)
        t += 60.0 / bpm
    return out


def wow_beat_times(
    bpm: float,
    seconds: float,
    depth: float = 0.015,
    rate_hz: float = 0.5,
    offset: float = 0.1,
) -> list[float]:
    """
    Beat times for a "floating" vinyl rip: sinusoidal tempo modulation.

    ``depth`` is the peak tempo deviation (0.015 = +/-1.5%), ``rate_hz`` the
    wow frequency (belt drift / warped record is around 0.3-1 Hz).
    """
    out: list[float] = []
    t = offset
    while t < seconds:
        out.append(t)
        inst = bpm * (1.0 + depth * np.sin(2 * np.pi * rate_hz * t))
        t += 60.0 / inst
    return out


def gap_beat_times(
    bpm: float, bars_before: int, bars_gap: int, bars_after: int, offset: float = 0.1
) -> tuple[list[float], list[bool], float]:
    """
    One continuous grid with a silent hole in the middle (the coast test).

    Returns (audible_beat_times, audible_is_downbeat, total_seconds). The grid
    itself never stops - the middle ``bars_gap`` bars are simply not rendered -
    so a tracker that coasts correctly re-enters in phase.
    """
    period = 60.0 / bpm
    total_beats = (bars_before + bars_gap + bars_after) * 4
    times: list[float] = []
    downs: list[bool] = []
    for k in range(total_beats):
        bar = k // 4
        if bars_before <= bar < bars_before + bars_gap:
            continue
        times.append(offset + k * period)
        downs.append(k % 4 == 0)
    seconds = offset + total_beats * period + 1.0
    return times, downs, seconds


def default_downbeats(times: list[float]) -> list[bool]:
    """Every 4th beat is a downbeat, starting at the first."""
    return [i % 4 == 0 for i in range(len(times))]


def render(
    times: list[float],
    downbeats: list[bool],
    seconds: float,
    sample_rate: int = 48000,
    hats: bool = False,
    noise_db: float | None = None,
    seed: int = 1234,
) -> np.ndarray:
    """
    Render int16 mono PCM: kick on each beat, optional offbeat hats + pink-ish noise.

    :param noise_db: noise level in dB relative to the kick amplitude (e.g. -12).
    """
    n = int(seconds * sample_rate)
    audio = np.zeros(n, dtype=np.float64)

    kick_len = int(KICK_DECAY_S * 4 * sample_rate)
    tt = np.arange(kick_len) / sample_rate
    kick = np.sin(2 * np.pi * KICK_HZ * tt) * np.exp(-tt / KICK_DECAY_S)

    for t, down in zip(times, downbeats):
        start = int(t * sample_rate)
        if start >= n:
            break
        seg = kick[: n - start]
        audio[start : start + seg.size] += seg * KICK_AMP * (DOWNBEAT_GAIN if down else 1.0)

    if hats:
        rng = np.random.default_rng(seed + 1)
        hat_len = int(HAT_LEN_S * sample_rate)
        ht = np.arange(hat_len) / sample_rate
        env = np.exp(-ht / (HAT_LEN_S / 3))
        periods = np.diff(times)
        for i, t in enumerate(times[:-1]):
            off = t + periods[i] / 2.0
            start = int(off * sample_rate)
            if start >= n:
                break
            burst = rng.standard_normal(min(hat_len, n - start))
            tone = np.sin(2 * np.pi * HAT_HZ * ht[: burst.size])
            audio[start : start + burst.size] += (
                (0.6 * burst + 0.4 * tone) * env[: burst.size] * HAT_AMP
            )

    if noise_db is not None:
        rng = np.random.default_rng(seed)
        white = rng.standard_normal(n)
        # 1/sqrt(f) shaping in the frequency domain = pink noise (enough energy
        # down low to be a realistic floor for the bass-band flux without being
        # a beat). Vectorised - a sample loop here would take seconds.
        spec = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
        freqs[0] = freqs[1] if freqs.size > 1 else 1.0
        pink = np.fft.irfft(spec / np.sqrt(freqs), n)
        pink /= max(1e-9, np.max(np.abs(pink)))
        audio += pink * KICK_AMP * (10.0 ** (noise_db / 20.0))

    peak = np.max(np.abs(audio))
    if peak > 0.99:
        audio *= 0.99 / peak
    return (audio * 32767.0).astype(np.int16)


def render_delay_trap(
    times: list[float],
    downbeats: list[bool],
    seconds: float,
    sample_rate: int = 48000,
    seed: int = 77,
) -> np.ndarray:
    """
    Soft kick + prominent dotted-eighth delay pings (the melodic-house trap).

    The pings repeat every 3/4 of a beat, which makes 4/3 of the true tempo
    score almost as well as the tempo itself in an autocorrelation tracker.
    """
    audio = render(times, downbeats, seconds, sample_rate).astype(np.float64) / 32767.0
    audio *= 0.5  # soften the kick like a melodic-house mix
    ping_len = int(0.030 * sample_rate)
    tt = np.arange(ping_len) / sample_rate
    ping = np.sin(2 * np.pi * 900.0 * tt) * np.exp(-tt / 0.010) * 0.55
    n = audio.size
    if len(times) >= 2:
        step = (times[1] - times[0]) * 0.75
        t = times[0]
        while t < seconds:
            start = int(t * sample_rate)
            if start >= n:
                break
            seg = ping[: n - start]
            audio[start : start + seg.size] += seg
            t += step
    peak = np.max(np.abs(audio))
    if peak > 0.99:
        audio *= 0.99 / peak
    return (audio * 32767.0).astype(np.int16)


def noise_only(seconds: float, sample_rate: int = 48000, seed: int = 99) -> np.ndarray:
    """Beatless noise for the false-lock test."""
    return render([], [], seconds, sample_rate, noise_db=0.0, seed=seed)


def to_us(times: list[float]) -> list[int]:
    """Seconds -> integer microseconds."""
    return [int(round(t * 1_000_000)) for t in times]
