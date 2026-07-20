# hue-box Phase 2 — raw PCM input (Winamp / foobar2000 / any source)

Self-contained plan for a future session. Phase 1 (this repo) receives ready-made
feature frames from Music Assistant. Phase 2 makes the box work **without MA**: it
ingests raw PCM from a PC (Winamp, foobar2000, anything) and computes the features
itself, then feeds the SAME effects engine through the SAME fan-in path.

## Goal

`Winamp/foobar (Windows PC) → network PCM → hue-box (Ubuntu/QNAP) → Hue lights`,
with zero Music Assistant on the network. Nothing changes in `effects/hue_fx/`
(the 1:1 rule holds) — only the *feature source* is new.

## What the engine consumes (the contract that must be reproduced)

From `python/hue_box_main.py` (same as MA provider `bridge.py`):

- `apply_spectrum(bins, timestamp_us)` — **17 mel-scale display bins**, uint16
  0–65535, 20–20000 Hz, ~20 Hz rate (`hue_fx/constants.py`: SPECTRUM_BINS/SCALE/F_MIN/F_MAX)
- `apply_peak(strength, timestamp_us)` — onset strength uint8 0–255
- `push_beats([BeatTiming])` — `timestamp_us` + `is_downbeat` (may be absent: the
  engine already degrades gracefully, peaks are the designed fallback)
- timestamps in µs on ONE monotonic clock; `render(now_us + latency)` unchanged

## Where MA's actual DSP lives (for the 1:1 port)

1. **Realtime spectrum/peak extractor — NOT in the server repo.** It is in the PyPI
   package `aiosendspin[server]==6.1.0`, module
   `aiosendspin/server/roles/visualizer/features.py`
   (`VisualizerFeatureExtractor` / `ExtractedFrame`). Usage contract:
   `server/music_assistant/providers/sendspin/synchronizer_role.py:151–167` —
   constructed with `sample_rate=48000, channels=2`, fed **25 ms** PCM chunks via
   `process_chunk(pcm_bytes, timestamp_us) -> list[ExtractedFrame]`.
   Pitch/YINFFT exists but MA disables it (`providers/sendspin/provider.py:110–113`)
   — do NOT port it.
2. **Beats/downbeats are OFFLINE in MA** — `smart_fades` provider:
   `providers/smart_fades/provider.py:377–412` (`_infer_beat_timings`, "Beat This"
   neural model, torch), `feature_extractor.py:17–105`, `dbn_postprocessor.py:13`.
   Torch — NOT portable to the box. Do not port; see beat strategy below.

## Plan

### 2a — spectrum + peaks (ship first)

1. **Ingest: VBAN UDP receiver (recommended).**
   - VBAN = Voicemeeter's trivial UDP PCM protocol (28-byte header + PCM frames).
   - PC side needs zero custom code: Voicemeeter (or VBAN Talkie) routes ANY
     Windows app — Winamp, foobar, browser — to the box. foobar also has VBAN
     output components.
   - Receiver is ~200 lines of C++ (or Python for the M0-style proof): parse
     header (sample rate code, channels, bit depth), reassemble PCM.
   - Alternatives, in order of effort: ALSA line-in capture (zero PC software,
     needs a cable) · custom Winamp `out_`/`dsp_` plugin streaming PCM over TCP
     (lowest latency, Winamp-only) · DLNA renderer mode (heavy: decode + control
     plane).
2. **Timestamping.** No Sendspin clock here: stamp PCM at ingest with the local
   monotonic clock (µs), keep a small jitter buffer (~2 × 25 ms), resample
   everything to canonical **48 kHz / 16-bit / stereo** at ingest (miniaudio or
   libsoxr).
3. **Feature extraction — reuse MA's own code 1:1** (primary strategy):
   `pip install aiosendspin==6.1.0` in the box's Python, instantiate
   `VisualizerFeatureExtractor` exactly as `synchronizer_role.py:151` does, feed
   25 ms chunks, forward each `ExtractedFrame` into the existing
   `_on_visualizer_frames`-equivalent path.
   - FIRST: audit `aiosendspin/server/roles/visualizer/features.py` runtime deps
     on the target (numpy? scipy?). If it needs heavy native wheels that don't
     exist for armv7/old-glibc, fallback = C++ extractor written against the
     Sendspin **spec** (https://github.com/Sendspin/spec), validated A/B against
     the Python one on the build box.
4. **Wire-up:** a `[input]` TOML section (`source = "sendspin" | "vban"`, port,
   etc.); `hue_box_main.py` grows a VBAN task that replaces the SendspinClient
   when selected. Stream start/end = VBAN packets appearing/disappearing
   (silence timeout ≈ the 20 s grace).

### 2b — beats (optional, later)

- MA's beat stack is torch-based and offline — not portable.
- 2a already works beat-less: onsets/peaks are the engine's designed fallback
  (pulse/fire fall back to virtual beats; auto-strobe still gates on energy).
- For real beats: **aubio `tempo` C API** (small, C, builds everywhere) or BTrack,
  synthesizing `BeatTiming` with a 4-count downbeat heuristic; feed through
  `push_beats` unchanged.

## Acceptance tests

1. foobar → Voicemeeter/VBAN → box → lights react, with MA absent from the LAN
   (packet capture shows only VBAN + DTLS).
2. A/B the same track via MA (Phase 1) vs VBAN (Phase 2a): base modes look
   equivalent side by side.
3. 2b: auto-strobe engages on the drops of a reference techno track.
4. CPU budget: extractor + engine + DTLS within budget on a Pi-4-class ARM box.

## Environment notes (from Phase 1)

- Build box: any Ubuntu 24.04 with llvm-mingw (Windows front-ends) + native/cross
  toolchains. Deploy targets: QNAP Ubuntu 14.04 userland, glibc 2.19 floor,
  x86_64/armv7/armv8, shipped as a `.qpkg`.
- Embedded Python must be ≥ 3.14 (the effects files use PEP 758 `except A, B:`).
- `effects/hue_fx/` is synced verbatim by `tools/sync_effects.py` — never edit it here.
