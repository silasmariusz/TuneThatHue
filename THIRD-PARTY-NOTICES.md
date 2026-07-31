# Third-party notices & attribution

TuneThatHue is Apache-2.0 licensed and stands on the shoulders of other
Apache-2.0 projects. This file records what is reused and from where, as the
Apache License requires. Nothing here is a modified derivative unless stated —
the reused effects engine is a **verbatim, byte-for-byte copy** kept in sync
with upstream by `tools/sync_effects.py` (which records the source commit and a
SHA-256 manifest in `effects/MANIFEST.sha256`).

## The effects engine — `effects/hue_fx/`

A verbatim copy of the `hue_entertainment` provider's DSP/effects modules from
**Music Assistant** (music-assistant/server), Apache-2.0:

- `analyzer.py`, `structure.py`, `strobe_overlay.py`, `palettes.py`,
  `palettes.json`, `constants.py`

Copyright (c) The Open Home Foundation and Music Assistant contributors.
Source: https://github.com/music-assistant/server
License: Apache-2.0.

These files are **unmodified**; improvements are made upstream in Music Assistant
and pulled here with `tools/sync_effects.py`. This 1:1 copy is deliberate — it
lets effect development and Windows/Linux/QNAP emulation share exactly the same
engine the Music Assistant provider runs.

## The real-time feature extractor (Phase 2)

The daemon runs Music Assistant's own `VisualizerFeatureExtractor` from the
**aiosendspin** package (Apache-2.0) to turn raw PCM into the mel-spectrum /
onset features the engine expects.

Source: aiosendspin (PyPI), part of the Sendspin project. License: Apache-2.0.

## The Hue Entertainment transport

Bridge pairing, entertainment-area discovery and DTLS-PSK / HueStream streaming
use the **hue-entertainment** library (music-assistant/hue-entertainment),
Apache-2.0.

Source: https://github.com/music-assistant/hue-entertainment
License: Apache-2.0.

## NumPy (QNAP runtime)

The QNAP daemon's feature extractor needs **NumPy**. NumPy ships no wheel for
QNAP's old glibc + Python 3.14, so the `.qpkg` bundles a NumPy built from source
in a `manylinux2014` (glibc 2.17) container (see `qnap/README-BUILD.md`).

Source: NumPy (https://numpy.org). License: BSD-3-Clause.

## Toolchain

Windows front-ends are cross-compiled with **llvm-mingw**
(https://github.com/mstorsjo/llvm-mingw). Not distributed with this repo.

---

The full Apache License 2.0 text is in `LICENSE`. Trademarks (Music Assistant,
Sendspin, Philips Hue, QNAP) belong to their respective owners; TuneThatHue is an
independent, unaffiliated project.

## FFmpeg

The package carries a copy of **FFmpeg** (`runtime/ffmpeg-<arch>/ffmpeg`), used to decode
whatever format a sender chooses. It is built from unmodified FFmpeg sources with audio
decoders only - no video, no external libraries - and **without** `--enable-gpl` or
`--enable-nonfree`, so the binary is covered by the **LGPL v2.1 or later**.

    ffmpeg 7.1.1, https://ffmpeg.org
    Copyright (c) 2000-2025 the FFmpeg developers
    Licensed under the GNU Lesser General Public License version 2.1 or later.

The exact configure line is in `qnap/build_ffmpeg.sh`, and the sources are the released
tarball from ffmpeg.org, unpatched. As LGPL requires, the binary is a separate executable
invoked as a subprocess and can be replaced: put your own build at the same path, or
remove it and the daemon falls back to any `ffmpeg` on the system.
