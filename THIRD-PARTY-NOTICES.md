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

## Toolchain

Windows front-ends are cross-compiled with **llvm-mingw**
(https://github.com/mstorsjo/llvm-mingw). Not distributed with this repo.

---

The full Apache License 2.0 text is in `LICENSE`. Trademarks (Music Assistant,
Sendspin, Philips Hue, QNAP) belong to their respective owners; TuneThatHue is an
independent, unaffiliated project.
