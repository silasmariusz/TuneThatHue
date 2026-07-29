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

### Windows capture apps (pick one; installers in the releases)

- **`soundrecorder/` — TuneThatHue SoundRecorder** *(recommended)*: system-tray app
  that captures audio and sends it to the daemon. Four sources, chosen in its settings:
  1. **Default output** — everything you hear (any player, browser, game; DirectSound /
     XAudio2 / DirectX included, since Windows mixes them before the speakers),
  2. **A specific output device** — second sound card, HDMI, virtual cable,
  3. **An input device** — Stereo Mix / "What U Hear", line-in, microphone,
  4. **A single application** — WASAPI process loopback (Windows 10 2004+).

  *Known limit:* audio played in WASAPI **exclusive mode** bypasses the Windows mixer,
  so loopback records silence there — capture that app directly (source 4) or use
  another output device.
- **`winamp/`** — Winamp DSP plugin (`dsp_tunethathue.dll`), for Winamp only.
- **`foobar/`** — native foobar2000 component (`foo_tunethathue.dll`, packaged as
  `.fb2k-component`), for foobar2000 2.x (64-bit). Add it in
  *Preferences → Playback → DSP Manager*.

The old `systray/tth_capture.c` is the SoundRecorder's predecessor (default-output
loopback only) and is kept for reference.

### The daemon

- **`python/tth_phase2.py`** — the daemon: receives audio (VBAN/UDP), runs Music
  Assistant's own feature extractor + the effects engine, and streams to Hue.
  Serves a **browser control panel** (`--webui-port`, default 8080): live status,
  in-browser bridge pairing (auto-discovers the bridge, or enter the IP), area
  selection, output on/off, and effect settings — a headless box is set up
  entirely from the browser, no config-file editing.
- **`effects/hue_fx/`** — the effects engine, a verbatim copy (see below).

Every capture front-end and the daemon speak the same **VBAN/UDP** wire format
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

Then install **TuneThatHue SoundRecorder** (or the Winamp / foobar2000 plugin), point
it at the daemon's IP and port, and play music — your Hue Entertainment area reacts.
Pairing and all daemon settings are also available in the WebUI, so a headless box
needs no config file editing.

### Building the Windows apps yourself

```powershell
powershell -File tools\fetch_foobar_sdk.ps1   # once: fetch the foobar2000 SDK
powershell -File build-windows.ps1            # builds apps + installers
```

Needs Visual Studio Build Tools with the **Desktop development with C++** workload,
plus [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`)
for the installers — missing tools only skip their own step. Output lands in
`installers\Output\`.

> **Unsigned builds:** these binaries are not code-signed, so SmartScreen warns and
> some antivirus products quarantine them (Symantec Endpoint Protection does).
> Set `TTH_SIGN_CERT` / `TTH_SIGN_PASS` before building to sign everything.

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
