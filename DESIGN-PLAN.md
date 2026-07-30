# TuneThatHue panel — design plan

## The brief, pinned down

**Subject:** a workbench for building Hue light effects.
**Audience:** whoever is writing the effect — one person, at a desk, with the lights in
the room.
**The page's single job:** watch the lights react and turn a knob until it looks right.

That makes this a *tuning instrument*, not a dashboard. Everything below follows from it.

## The hard boundary

These 15 files are byte-identical copies of the Music Assistant provider and **must not
be touched, reformatted or imported from in a way that requires changing them**:

    effects/hue_fx/       analyzer, structure, strobe_overlay, palettes(.py/.json), constants
    effects/ma_provider/  __init__, provider, bridge, preview, calibration, strings.json,
                          manifest.json, README.md, resources/preview.html

`effects/ma_provider/resources/preview.html` is MA's own preview page. It is a
**reference to read**, never a file to edit or serve — it talks to MA's endpoints.

Everything new lives in files we own: `resources/webui.html`, `python/webui.py`,
`python/tth_phase2.py`, `python/ma_schema.py`.

## Direction

The subject's own world is lighting desks and DJ mixers, so the design borrows their
vernacular rather than dashboard conventions: dark surfaces because the light readout has
to dominate, channel strips instead of form rows, and meters that actually move.

The current panel is a near-black page with a cyan accent, which is the most generic dark
UI there is. The change is not the darkness, it is what carries the page: **the lights
themselves become the interface**, not a stat block above a form.

### Colour

The palette is deliberately almost colourless, because every saturated colour on screen
has to belong to a lamp. Chrome that competes with the light readout is the one thing
this page cannot afford.

    --desk        #0B0D10   the surface everything sits on
    --strip       #14181D   channel strip / card face
    --rule        #232A32   hairlines and strip separators
    --legend      #7C8899   silkscreen labels, the way a console prints them
    --read        #E8EDF3   readouts and values
    --live        #33E1A0   the single accent: "signal present", nothing else

`--live` is used **only** for live state (receiving, streaming, beat). It never decorates.

### Type

    Display / readouts   Barlow Condensed — condensed, technical, reads like a console
                         silkscreen; used for the light names and section legends
    Body                 Inter — the settings, descriptions, everything you read
    Numbers / meters     JetBrains Mono — BPM, packet rates, latency, tabular figures

Condensed + mono is the pairing a lighting desk actually uses. It is also not the
serif-display pairing that turns up on every AI-generated page.

Fonts must be **self-hosted or system-stacked** — the box runs on a NAS with no internet.

### Layout

    ┌──────────────────────────────────────────────────────────┐
    │ TuneThatHue                        ● receiving  ● streaming│
    ├──────────────────────────────────────────────────────────┤
    │  THE LIGHT WALL  (the hero — actual streamed frames)      │
    │  ┌──────┐┌──────┐┌──────┐┌──────┐                         │
    │  │      ││      ││      ││      │  each tile IS the light  │
    │  │      ││      ││      ││      │  colour, 30 fps          │
    │  ├──────┤├──────┤├──────┤├──────┤                         │
    │  │▒▒▒░░ ││▒░░   ││▒▒▒▒░ ││░     │  2 s persistence trail   │
    │  ├──────┤├──────┤├──────┤├──────┤                         │
    │  │ name ││ name ││ name ││ name │  silkscreen label        │
    │  │ ──○──││ ──○──││ ──○──││ ──○──│  per-light brightness    │
    │  └──────┘└──────┘└──────┘└──────┘                         │
    ├──────────────────────────────────────────────────────────┤
    │  SIGNAL CHAIN   audio ▸ beat ▸ engine ▸ bridge            │
    │  spectrum bars + bpm + rates, one honest row              │
    ├──────────────────────────────────────────────────────────┤
    │  SETTINGS (generated from the provider — untouched)       │
    │  BRIDGE · PALETTE · VFX · ADVANCED                        │
    ├──────────────────────────────────────────────────────────┤
    │  GET THE SENDERS  winamp · foobar · sound capture         │
    └──────────────────────────────────────────────────────────┘

The signal chain is drawn as a chain because it **is** one — audio arrives, a beat is
found, the engine renders, the bridge receives. That is real sequence, so showing it as
one is information, not decoration. (No 01/02/03 anywhere else: nothing else here is
ordered.)

### Signature

**The persistence trail.** Under each light tile, a thin strip shows the last two seconds
of that light's colour, scrolling. It turns a flash into something you can actually read:
you can see the strobe's rhythm, whether a build is recruiting lights one by one, and
whether a drop hit all of them together — none of which you can judge from a light that
is already dark again. It is only possible because we tee the real frames, and it is the
thing this page will be remembered by.

## Work threads (independent, can run in parallel)

**A. Frame tee + preview transport** — publish rendered frames from the render loop
(`tth_phase2.py`, one line at the existing send point) to an SSE endpoint in `webui.py`.
Rate-limit to ~20 fps for the browser. No MA file touched.

**B. The light wall** — tiles, trails, per-light brightness. Needs A.

**C. Signal chain strip** — spectrum, bpm, rates, state pills. Independent of A.

**D. Palette board** — swatch grid from `palette_names()`, click to pick, tick to add to
rotation, greyed while rotation is on. Independent.

**E. Latency calibration** — a metronome. MA plays it through a speaker; we have no
players, so the **browser** clicks via WebAudio while the lights flash, and you dial
latency until they line up. Independent.

**F. Downloads** — serve the three installers from the package with a fallback link to
the GitHub release, so it works on a NAS with no internet.

**G. Icons** — design the app icon (QNAP tile at 64/80 px + favicon + installer icon).
Independent of everything.

**H. Verification** — after each thread: load the page in a browser, screenshot, check it
renders and the control actually changes the lights. Not "it compiles".

## What shipped (2026-07-30)

All threads built and checked in a browser. Three things came out different from the
plan, each for a reason:

- **Per-light brightness is a meter, not a slider.** The engine takes a `per_light`
  dict, but nothing persists it, so a slider would have been a control that decides
  nothing. The tile shows the light's measured output instead.
- **The palette rotation board never appears** — the provider marks those entries
  hidden, and the screen follows the provider. The code is in place for the day they
  are unhidden; the `ROT` tick on a swatch already shows which palettes are in the
  stored list.
- **Fonts are system stacks, not Barlow/Inter/JetBrains.** The box has no internet and
  we ship no font files, so the stacks pick the nearest condensed / plain / mono faces
  the viewer already has.

Also fixed on the way: option titles now read "Pulse", not "pulse" (the schema parser
could not see `title=mode.capitalize()` inside the comprehension), and copy that
addresses Music Assistant's own screen ("click the (?) button") is dropped.

**Peak hold on the tile face.** Straight off the wire the faces were black in every
screenshot: at 126 BPM in Pulse mode a light is dark most of the time, so an
instantaneous readout shows nothing. The face now holds the peak and falls away over
~200 ms, like a meter. The trail underneath stays unheld - that is where the real
timing is read - and the level bar under the name is the true instantaneous value.

**`install_icons()` must delete before it copies.** Until an app ships its own icon the
panel leaves a *symlink* at `/home/httpd/RSS/images/<name>.gif` pointing at the shared
`no_qpkg_icon_64.gif`. `cp` writes through it and replaces the placeholder for every
other app on the NAS. Found by doing exactly that; the originals are not on GitHub and
were restored from a second NAS that had not been touched.

## Build

Built and signed with the QDK (`sh build.sh` in the assembled build root ->
`build/TuneThatHue_<ver>.qpkg`), then installed on a test NAS with `qpkg_cli -m` and
checked in a browser: 4 real lights in an Entertainment area, frames at 15 fps, no
console errors. The build root carries the signing keys - they stay there and never
reach a repo. x86_64 only, because that is the only runtime assembled; aarch64/armv7
need step 2 of `qnap/README-BUILD.md` run per arch.

## Quality floor

Responsive down to a phone, visible keyboard focus, `prefers-reduced-motion` respected
(the trail stops scrolling, tiles still update), and no external requests of any kind.
