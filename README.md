# TuneThatHue

Sync **Philips Hue** lights to whatever your PC (or a headless box) is playing —
Winamp, foobar2000, a browser, a game, anything — **without Home Assistant and
without a running Music Assistant server.**

TuneThatHue is a small "null audio player": it captures the sound, analyses it,
and streams colours to a Hue bridge over the Entertainment API (DTLS). It reuses
the **exact** effects engine from Music Assistant's `hue_entertainment` provider,
so the look matches — while running as a standalone daemon you can put on a PC, a
**Raspberry Pi**, or a **QNAP NAS**.

```
PC audio ──► capture (systray / Winamp plugin) ──VBAN/UDP──► TuneThatHue daemon
             │                                                │
             │                          Music Assistant's feature extractor (1:1)
             │                                                │
             └───────────────────────────────► effects engine (verbatim copy)
                                                              │
                                                       DTLS ──► Hue bridge ──► 💡
```

Status: the Windows capture front-ends and the Python daemon **work end-to-end on
real lights today.** The native C++ / `.qpkg` build for QNAP and ARM is the
roadmap below.

## Components

- **`systray/tth_capture.c`** — Windows system-tray app; captures ALL system audio
  (WASAPI loopback) and streams it to the daemon. Works with any player.
- **`winamp/dsp_tunethathue.c`** — a Winamp DSP plugin that does the same for Winamp.
- **`python/tth_phase2.py`** — the daemon: receives audio (VBAN/UDP), runs Music
  Assistant's own feature extractor + the effects engine, and streams to Hue.
  Serves a **browser control panel** (`--webui-port`, default 8080): live status,
  in-browser bridge pairing (auto-discovers the bridge, or enter the IP), area
  selection, output on/off, and effect settings — a headless box is set up
  entirely from the browser, no config-file editing.
- **`effects/hue_fx/`** — the effects engine, a verbatim copy (see below).

Both capture front-ends and the daemon speak the same **VBAN/UDP** wire format
(int16 PCM) plus a tiny `TTHP`/`TTHO` ping for the "Test connection" button, so
one daemon serves them all.

## Quick start (Windows PC → Hue)

```sh
# daemon (Python 3.14)
python3.14 -m venv venv
venv/Scripts/pip install -r python/requirements.txt cryptography
venv/Scripts/pip install hue-entertainment      # or -e a local checkout

# 1) pair with your bridge (press the round link button when asked)
venv/Scripts/python python/tth_phase2.py --pair --host <BRIDGE_IP>

# 2) run: drive the lights + open http://localhost:8080 to configure
venv/Scripts/python python/tth_phase2.py --output hue
```

Then run `systray/tth_capture.exe` (or enable the Winamp plugin) and play music —
your Hue Entertainment area reacts. Pairing and all settings are also available in
the WebUI, so a headless box needs no config file editing.

> The Hue bridge allows **one** Entertainment stream at a time. If another app
> (e.g. a Music Assistant add-on) is streaming to the same area, stop it first.

## Why the engine is a 1:1 copy (on purpose)

`effects/hue_fx/` is a **verbatim, byte-for-byte copy** of the Music Assistant
`hue_entertainment` provider's engine (`analyzer.py`, `structure.py`,
`strobe_overlay.py`, `palettes.py`, `palettes.json`, `constants.py`). It is never
edited here.

That is the whole point: keeping it identical means effect work developed and
**emulated on Windows/Linux** behaves exactly like the Music Assistant provider,
and the same engine drops straight onto a Raspberry Pi or QNAP box. Improvements
happen upstream in Music Assistant and are pulled in with:

```sh
python tools/sync_effects.py          # copy from a Music Assistant checkout + hash manifest
python tools/sync_effects.py --check  # verify no drift
```

`effects/MANIFEST.sha256` records the source commit and per-file hashes. The engine
is pure-stdlib Python (no numpy/asyncio) and needs **Python ≥ 3.14**.

## Targets & roadmap

| Target | How | Status |
|---|---|---|
| Windows (capture + daemon) | systray/Winamp exe + Python daemon | **working** |
| Linux / Raspberry Pi | Python daemon (any Py 3.14 box) | **working** (Python); native C++ later |
| QNAP NAS | native `.qpkg` (`tune-that-hue`) | roadmap |

Native-build ladder: **M1** C++ host embedding CPython 3.14 → **M2** C++ Sendspin
client → **M3** C++ DTLS-PSK (mbedTLS) + HueStream v2 + CLIP v2 (Python side becomes
stdlib-only) → **M4** `.qpkg` for x86_64 / armv7 / armv8 (glibc 2.19 floor). See
`PHASE2-RAW-PCM.md` for the raw-PCM/feature-extractor details.

Windows binaries are cross-compiled with [llvm-mingw](https://github.com/mstorsjo/llvm-mingw)
via `build-windows.sh`.

## Licence & attribution

TuneThatHue is **Apache-2.0** (see `LICENSE`). It reuses Apache-2.0 code from the
Music Assistant project (the effects engine and the audio feature extractor) and
the `hue-entertainment` library — full details in `THIRD-PARTY-NOTICES.md`.

Music Assistant, Sendspin, Philips Hue and QNAP are trademarks of their respective
owners. **TuneThatHue is an independent project, not affiliated with or endorsed by
any of them.**

TuneThatHue © 2025–2026 Silas Mariusz Grzybacz · [devspark.pl](https://devspark.pl)
· published: forum.qnap.net.pl · QNAP app repo: myqnap.org
