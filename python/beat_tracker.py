"""
Real-time beat tracker for the TuneThatHue daemon.

Music Assistant gets its beats from an offline neural model (Beat This, torch)
that runs over a whole track on the server. A standalone box has neither the
track nor torch, so this derives the beat grid live from the audio itself:

    PCM -> onset envelope (spectral flux) -> tempo (autocorrelation + comb)
        -> beat phase -> a predicted beat grid pushed AHEAD of playback

The effects engine consumes a *schedule*: it looks for the beat before and the
beat after the frame it is rendering and interpolates between them. So beats
must be handed over before they are heard, which is what emit() does.

Only numpy is required (already needed by the feature extractor).

Accuracy note: this is a classic DSP tracker, not the neural model MA uses. On
steady 4/4 material (house, techno, pop) it locks tightly; on rubato/orchestral
music it will drift or stay unlocked - in which case it emits nothing rather
than firing the lights at random.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

# --- analysis geometry -------------------------------------------------------
_FRAME = 1024          # FFT window
_HOP = 512             # ~10.7 ms at 48 kHz - fine enough to place a beat
_HISTORY_S = 8.0       # onset history used for tempo/phase estimation
_MIN_BPM = 70.0
_MAX_BPM = 185.0
# Autocorrelation alone cannot tell a tempo from its double or half, so the comb
# score is weighted by how plausible the tempo is for music. Centred on typical
# dance tempo, wide enough not to fight a genuinely slow or fast track.
_PRIOR_BPM = 125.0
_PRIOR_OCTAVES = 0.9
# Bins used for the "where is the beat" envelope. Broadband hits (hi-hats, snare
# noise) dominate a full-spectrum flux and would pull the phase onto the offbeat,
# so phase and downbeat are decided on the low end, where the kick lives.
_LOW_HZ = 250.0
# Frame->time correction, in ODF frames. Measured by sweeping click tracks of
# known tempo until the signed timing error centred on zero (see the tracker
# self-test); it cancels the analysis-window lag.
_TIME_CORRECTION_FRAMES = 2.5
# The beat phase is fitted on this much recent audio (tempo still uses the full
# history, which needs the length to resolve the period).
_PHASE_WINDOW_S = 3.0
# Re-estimate tempo/phase at this interval; between estimates the grid is simply
# extrapolated, which is what keeps the pulse steady.
_ESTIMATE_EVERY_S = 0.5
# How far ahead beats are handed to the engine (it needs the *next* beat to
# interpolate towards; a beat or two of lookahead is plenty).
_LOOKAHEAD_S = 1.2
# Autocorrelation peak strength (relative to the mean) below which we treat the
# signal as "no clear beat" and stay silent.
_LOCK_THRESHOLD = 3.8
# A new estimate must beat the running one by this much to re-latch the tempo,
# so a single noisy window cannot yank a locked grid around.
_RELATCH_MARGIN = 1.15
# Grid continuity. An accepted estimate within this period drift of the running
# grid is a REFINEMENT: the period is blended and the new phase snapped onto the
# old grid (a +/-1-beat slip becomes impossible). Beyond it - or after the coast
# deadline - it is a hard latch: a genuinely new grid.
_REFINE_MAX_DRIFT = 0.08
_PERIOD_BLEND = 0.30   # EMA weight of the new period measurement on refinement
_PHASE_BLEND = 0.35    # fraction of the measured phase residual applied per estimate
# The downbeat may move only after this many consecutive estimates agree on the
# same new position - one noisy window must never flip the "1".
_BAR_HYSTERESIS = 3
# A downbeat measurement only counts as a vote when the winning bar position
# beats the runner-up by this fraction; near-ties (grid slightly misaligned
# over the long history) otherwise wobble between adjacent positions.
_BAR_MARGIN = 0.15
# After losing lock the last grid keeps emitting (pure extrapolation) for this
# many bars, so a breakdown dims the music without killing the pulse.
_COAST_BARS = 16
# A re-based grid resumes emission no closer than this (in periods) to the last
# emitted beat, so the near-twin of an already-fired beat cannot fire again.
_RESUME_GUARD = 0.5


class BeatTracker:
    """
    Feeds on interleaved int16 PCM and returns predicted beats.

    :param sample_rate: PCM sample rate in Hz.
    :param channels: number of interleaved channels.
    """

    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = max(1, int(channels))
        self.odf_rate = self.sample_rate / _HOP
        self._history = int(_HISTORY_S * self.odf_rate)
        # Diagnostic tap for the bench/tests: called with a dict per estimate
        # decision when set. Never used by the daemon; zero cost when None.
        self.on_event = None
        self.reset()

    def _notify(self, kind: str, **fields) -> None:
        """Report a tracker decision to the diagnostic tap, if attached."""
        if self.on_event is not None:
            fields["kind"] = kind
            fields["frame"] = self._frames_done
            self.on_event(fields)

    def reset(self) -> None:
        """Forget all state (new stream, or a gap in the audio)."""
        self._pcm = np.zeros(0, dtype=np.float32)
        self._prev_mag: np.ndarray | None = None
        self._odf = np.zeros(0, dtype=np.float32)      # full band -> tempo
        self._odf_low = np.zeros(0, dtype=np.float32)  # bass only -> phase, downbeat
        self._low_bins = max(2, int(_LOW_HZ / (self.sample_rate / _FRAME)))
        self._frames_done = 0          # ODF frames produced since the anchor
        self._anchor_us: int | None = None
        self._samples_seen = 0
        self._window = np.hanning(_FRAME).astype(np.float32)

        self._period: float | None = None   # beat period, in ODF frames
        self._phase: float = 0.0            # frame index of a beat, mod period
        self._confidence: float = 0.0
        self._bar_offset: int = 0           # which beat of 4 is the downbeat
        self._beat_index: int | None = None # next beat to emit, in beats since phase
        self._frames_at_last_estimate = -10**9
        # Emission frontier: absolute frame of the last emitted beat. A re-based
        # grid resumes strictly past it, so no beat is ever handed out twice.
        self._emitted_until: float = -1e18
        # Committed downbeat identity as an absolute grid frame (survives phase
        # re-basing), plus the hysteresis candidate for moving it.
        self._downbeat_frame: float | None = None
        self._bar_candidate: int | None = None
        self._bar_candidate_count: int = 0
        # Emission keeps extrapolating the last grid until this frame after the
        # last accepted estimate (the breakdown coast window).
        self._coast_until: float = -1e18

    # -- public API ----------------------------------------------------------

    @property
    def bpm(self) -> float:
        """Current tempo estimate (0.0 when not locked)."""
        if not self._period:
            return 0.0
        return 60.0 * self.odf_rate / self._period

    @property
    def locked(self) -> bool:
        """True while a beat grid is being tracked confidently."""
        return self._period is not None and self._confidence >= _LOCK_THRESHOLD

    @property
    def coasting(self) -> bool:
        """True while emitting an extrapolated grid after losing lock."""
        return (
            not self.locked
            and self._period is not None
            and self._frames_done <= self._coast_until
        )

    def process(self, pcm: bytes, chunk_start_us: int) -> list[SimpleNamespace]:
        """
        Consume one PCM chunk and return beats to schedule (possibly empty).

        :param pcm: interleaved int16 PCM.
        :param chunk_start_us: timestamp of the chunk's first sample, in the same
            clock the renderer uses.
        """
        if self._anchor_us is None:
            self._anchor_us = chunk_start_us
            self._samples_seen = 0

        self._append(pcm)
        self._advance_odf()

        # Re-estimate periodically - not every chunk, which would be wasteful
        # and would also make the grid jitter.
        if self._frames_done - self._frames_at_last_estimate >= _ESTIMATE_EVERY_S * self.odf_rate:
            self._frames_at_last_estimate = self._frames_done
            self._estimate()

        return self._emit()

    # -- onset envelope ------------------------------------------------------

    def _append(self, pcm: bytes) -> None:
        """Downmix a chunk to mono float and queue it for framing."""
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return
        if self.channels > 1:
            usable = (samples.size // self.channels) * self.channels
            mono = samples[:usable].reshape(-1, self.channels).mean(axis=1)
        else:
            mono = samples
        self._pcm = np.concatenate((self._pcm, mono.astype(np.float32) / 32768.0))

    def _advance_odf(self) -> None:
        """Turn buffered PCM into onset-envelope frames (log spectral flux)."""
        new: list[float] = []
        new_low: list[float] = []
        while self._pcm.size >= _FRAME:
            frame = self._pcm[:_FRAME] * self._window
            mag = np.abs(np.fft.rfft(frame))
            # Log compression keeps quiet transients meaningful next to loud ones.
            mag = np.log1p(mag * 100.0)
            if self._prev_mag is not None:
                # Half-wave rectified flux: only energy *increases* mark onsets.
                rise = np.maximum(mag - self._prev_mag, 0.0)
                new.append(float(rise.sum()))
                new_low.append(float(rise[: self._low_bins].sum()))
            self._prev_mag = mag
            self._pcm = self._pcm[_HOP:]
            self._samples_seen += _HOP

        if new:
            self._odf = np.concatenate((self._odf, np.asarray(new, dtype=np.float32)))
            self._odf_low = np.concatenate((self._odf_low, np.asarray(new_low, dtype=np.float32)))
            self._frames_done += len(new)
            if self._odf.size > self._history:
                self._odf = self._odf[-self._history :]
                self._odf_low = self._odf_low[-self._history :]

    # -- tempo + phase -------------------------------------------------------

    def _estimate(self) -> None:
        """Re-estimate tempo and beat phase from the onset history."""
        n = self._odf.size
        if n < int(3.0 * self.odf_rate):  # need a few seconds before guessing
            return

        # Tempo is read from the bass envelope where there is one. Off-beat
        # hi-hats make the full-band envelope just as periodic at double tempo,
        # which is how a 90 BPM track gets reported as 180; the kick does not
        # have that ambiguity. Fall back to full band for bass-less material.
        src = self._odf_low if float(self._odf_low.sum()) > 1e-6 else self._odf
        odf = src - src.mean()
        if not np.any(odf):
            self._confidence = 0.0
            return

        # Autocorrelation via FFT (cheap, and we only need positive lags).
        size = 1 << int(np.ceil(np.log2(n * 2)))
        spec = np.fft.rfft(odf, size)
        acf = np.fft.irfft(spec * np.conj(spec), size)[:n]
        if acf[0] <= 0:
            self._confidence = 0.0
            return
        acf /= acf[0]

        min_lag = max(2, int(60.0 * self.odf_rate / _MAX_BPM))
        max_lag = min(n - 1, int(60.0 * self.odf_rate / _MIN_BPM))
        if max_lag <= min_lag:
            return

        # Comb score: a real beat period also shows correlation at 2x/3x/4x the
        # lag, which is what separates the true beat from half/double tempo.
        lags = np.arange(min_lag, max_lag + 1)
        score = np.zeros(lags.size, dtype=np.float64)
        for mult in (1, 2, 3, 4):
            idx = lags * mult
            valid = idx < n
            score[valid] += acf[idx[valid]] / mult

        # Tempo prior: without it the peak at half or double the real tempo wins
        # about as often as the right one.
        bpms = 60.0 * self.odf_rate / lags
        score *= np.exp(-0.5 * (np.log2(bpms / _PRIOR_BPM) / _PRIOR_OCTAVES) ** 2)

        best = int(np.argmax(score))
        period = float(lags[best])
        # How far the peak stands out from the rest of the curve, in standard
        # deviations. A plain ratio cannot be used: correlation at unrelated lags
        # is negative, so the baseline is negative and ratios flip sign.
        strength = float((score[best] - np.median(score)) / (score.std() + 1e-9))

        # Parabolic interpolation around the peak for sub-frame resolution.
        if 0 < best < score.size - 1:
            a, b, c = score[best - 1], score[best], score[best + 1]
            denom = a - 2 * b + c
            if denom != 0:
                period += 0.5 * (a - c) / denom

        if strength < _LOCK_THRESHOLD:
            # Nothing convincing - decay confidence but keep extrapolating the
            # old grid for a while, so a quiet bar does not kill the pulse.
            self._confidence *= 0.7
            self._notify("est", accepted=False, reason="weak", conf=strength)
            return

        # Only re-latch when clearly better, or when the tempo barely moved
        # (that case is a refinement of the same grid, not a new one).
        if self._period is not None and self._confidence >= _LOCK_THRESHOLD:
            drift = abs(period - self._period) / self._period
            if drift > 0.04 and strength < self._confidence * _RELATCH_MARGIN:
                self._notify("est", accepted=False, reason="relatch", conf=strength)
                return

        # A drift within _REFINE_MAX_DRIFT of a still-coasting grid is a
        # refinement of the SAME grid; anything else replaces it wholesale.
        refine = (
            self._period is not None
            and abs(period - self._period) / self._period <= _REFINE_MAX_DRIFT
            and self._frames_done <= self._coast_until
        )
        if refine:
            prev_period = self._period
            self._period = prev_period + _PERIOD_BLEND * (period - prev_period)
        else:
            prev_period = None
            self._period = period
            self._downbeat_frame = None
            self._bar_candidate = None
            self._bar_candidate_count = 0
        self._confidence = strength
        self._update_phase(prev_period)
        self._coast_until = self._frames_done + _COAST_BARS * 4 * self._period
        self._notify(
            "est",
            accepted=True,
            refine=bool(refine),
            bpm=self.bpm,
            conf=strength,
            phase=self._phase,
            period_frames=self._period,
            downbeat_frame=self._downbeat_frame,
        )

    def _update_phase(self, prev_period: float | None = None) -> None:
        """
        Find which offset within the period the beats actually fall on.

        With ``prev_period`` set this is a refinement of the running grid: the
        measured phase is snapped onto the nearest beat of the OLD grid and only
        a fraction of the residual is applied, so the pulse slides instead of
        tearing and a +/-1-beat slip cannot happen.
        """
        if not self._period:
            return
        period = self._period
        # Phase is decided on the bass envelope: in the music this is for, the
        # beat is where the kick is, while broadband hats sit on the offbeat.
        env_full = self._odf_low if float(self._odf_low.sum()) > 0.0 else self._odf
        n_full = env_full.size

        # Fit the phase on RECENT audio only. Over the full history a period that
        # is off by even a fraction of a percent makes the best-fit grid a
        # compromise across the whole window, which lands the extrapolated beats
        # tens of milliseconds out. A few seconds is plenty to place the phase.
        tail_n = min(n_full, max(int(round(period * 4)), int(_PHASE_WINDOW_S * self.odf_rate)))
        env = env_full[-tail_n:]
        n = env.size

        offsets = np.arange(0, int(round(period)))
        if offsets.size == 0:
            return
        best_offset, best_score = 0, -1.0
        for off in offsets:
            idx = np.round(np.arange(off, n, period)).astype(int)
            idx = idx[idx < n]
            if idx.size == 0:
                continue
            s = float(env[idx].sum() / idx.size)
            if s > best_score:
                best_score, best_offset = s, int(off)

        tail_start = self._frames_done - n
        phi_meas = float(tail_start + best_offset)
        now_frame = self._frames_done

        if prev_period is None or self._beat_index is None:
            self._phase = phi_meas
        else:
            # Refinement: re-express the old anchor on the beat nearest "now"
            # (with the OLD period - projecting with the blended one would
            # amplify the period delta), then snap the measurement onto that
            # grid and glide a fraction of the residual.
            phi_old = self._phase + round((now_frame - self._phase) / prev_period) * prev_period
            k_star = round((phi_meas - phi_old) / period)
            phi_pred = phi_old + k_star * period
            self._phase = phi_pred + _PHASE_BLEND * (phi_meas - phi_pred)

        # Downbeat needs several bars, so it keeps using the long history. The
        # history beat grid is derived from the COMMITTED phase (not the raw
        # measurement), so the env positions and the k0 index mapping agree -
        # otherwise the measured bar position wobbles near beat boundaries.
        hist_start = self._frames_done - n_full
        first = self._phase + np.ceil((hist_start - self._phase) / period) * period - hist_start
        beats = np.round(np.arange(first, n_full, period)).astype(int)
        beats = beats[(beats >= 0) & (beats < n_full)]
        measured_bar: int | None = None
        if beats.size >= 8:
            sums = [float(env_full[beats[b::4]].sum()) for b in range(4)]
            ranked = sorted(sums, reverse=True)
            # Vote only on a clear win; a near-tie is measurement noise.
            if ranked[0] > 0 and (ranked[0] - ranked[1]) / ranked[0] >= _BAR_MARGIN:
                # Express the winning bar position relative to the emission grid.
                k0 = int(round((hist_start + beats[0] - self._phase) / period))
                measured_bar = (int(np.argmax(sums)) + k0) % 4
        self._commit_bar_offset(measured_bar, period)

        # Resume emission past the frontier: never re-emit a beat already handed
        # out, and skip the near-twin of the last one on a re-based grid.
        start = max(now_frame, self._emitted_until + _RESUME_GUARD * period)
        self._beat_index = int(np.ceil((start - self._phase) / period))

    def _commit_bar_offset(self, measured: int | None, period: float) -> None:
        """
        Keep the committed downbeat stable across re-estimates (hysteresis).

        The committed "1" lives ONLY as an absolute grid frame
        (``_downbeat_frame``) - never as an index relative to the phase, which
        re-bases on every estimate and would make the numbering rotate. A
        measured disagreement must repeat ``_BAR_HYSTERESIS`` times in a row
        (same disagreement, in beats) before the downbeat moves.
        """
        if measured is None:
            return
        # The measured bar position as an absolute frame on the current grid.
        k = int(np.ceil((self._frames_done - self._phase) / period))
        k += (measured - k) % 4
        proposal = self._phase + k * period

        if self._downbeat_frame is None:
            self._downbeat_frame = proposal
            self._bar_candidate = None
            self._bar_candidate_count = 0
            return
        bar = 4.0 * period
        beats_off = int(round(((proposal - self._downbeat_frame) % bar) / period)) % 4
        if beats_off == 0:
            self._downbeat_frame = proposal  # same "1": refresh, killing drift
            self._bar_candidate = None
            self._bar_candidate_count = 0
            return
        if beats_off == self._bar_candidate:
            self._bar_candidate_count += 1
        else:
            self._bar_candidate = beats_off
            self._bar_candidate_count = 1
        if self._bar_candidate_count >= _BAR_HYSTERESIS:
            self._downbeat_frame = proposal
            self._bar_candidate = None
            self._bar_candidate_count = 0

    # -- emission ------------------------------------------------------------

    def _frame_to_us(self, frame: float) -> int:
        """
        Convert an absolute ODF frame index to a wall timestamp.

        An onset only shows up in the analysis frame *after* it starts, so the
        raw frame index reads early; _TIME_CORRECTION_FRAMES compensates (the
        value was measured against click tracks of known tempo).
        """
        assert self._anchor_us is not None
        samples = (frame + _TIME_CORRECTION_FRAMES) * _HOP
        return int(self._anchor_us + samples * 1_000_000 / self.sample_rate)

    def _emit(self) -> list[SimpleNamespace]:
        """Hand over any beats that fall inside the lookahead window."""
        if self._period is None or self._beat_index is None:
            return []
        # Locked, or coasting: a breakdown keeps the pulse extrapolating for a
        # bounded number of bars instead of going silent and re-anchoring cold.
        if not self.locked and self._frames_done > self._coast_until:
            return []
        out: list[SimpleNamespace] = []
        horizon = self._frames_done + _LOOKAHEAD_S * self.odf_rate
        # Cap the loop: a bad period must never spin here.
        for _ in range(16):
            frame = self._phase + self._beat_index * self._period
            if frame > horizon:
                break
            if self._downbeat_frame is not None:
                # Compare against the absolute committed "1", not an index: the
                # phase re-bases on refinements and index numbering rotates.
                is_downbeat = round((frame - self._downbeat_frame) / self._period) % 4 == 0
            else:
                is_downbeat = (self._beat_index % 4) == (self._bar_offset % 4)
            out.append(
                SimpleNamespace(timestamp_us=self._frame_to_us(frame), is_downbeat=is_downbeat)
            )
            self._emitted_until = frame
            self._beat_index += 1
        return out
