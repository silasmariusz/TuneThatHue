# TuneThatHue — "DECK" design guidelines

**Traktor Pro 4 chassis · cassette-deck aluminum faceplates.** Strict rules. Every new
screen, control or icon must pass every rule in this file, or it does not ship.

## 1. The concept, in one paragraph

The page is a piece of rack hardware. The **chassis** is Traktor's near-black housing:
header, footer, page background, module rails. Bolted into it are **modules** — rack
units whose faces are light-grey **brushed aluminum**, printed with dark-ink silkscreen,
the way a Technics or Nakamichi cassette deck front panel is. Cut into the aluminum are
**dark glass screens** (the light wall, the spectrum, the counters): the only place
saturated colour is allowed, because on this page every saturated colour must belong to
a lamp or a signal. Three materials, never mixed: chassis, aluminum, glass.

## 2. Tokens — the only colours that exist

Never introduce a hex value outside this table. If a new colour seems needed, the design
is wrong, not the table.

| Token | Value | Role — and nothing else |
|---|---|---|
| `--chassis` | `#141619` | page background |
| `--chassis-2` | `#1B1E22` | header/footer strips, glyph knockouts |
| `--rail-hi` / `--rail-lo` | `#34393F` / `#23272B` | module title rail gradient (top→bottom) |
| `--rail-ink` | `#C9CED4` | text printed on rails |
| `--rail-dim` | `#7D858D` | secondary rail/footer text |
| `--alu-hi` / `--alu` / `--alu-lo` | `#EAEBEC` / `#D3D6D8` / `#BEC2C5` | faceplate gradient (top→bottom) |
| `--ink` | `#26292C` | primary silkscreen print on aluminum |
| `--ink-soft` (= `--legend`) | `#565D63` | secondary print, captions, notes |
| `--screen` | `#0B0D0F` | glass inset background |
| `--read` | `#DDE3E8` | text on glass |
| `--vfd` | `#FFB868` | numeric readouts on glass only (amber VFD digits) |
| `--deck-a/b/c/d` | `#2FA8DE` `#8DC63F` `#AEB6BD` `#F0862F` | signal-chain deck caps **only** |
| `--live` | `#33E1A0` | "signal present": LEDs, level bars, spectrum. Never decoration |
| `--hot` | `#FFB020` | warnings only |
| `--accent` | `#F0862F` | primary keys, selected pads, focus rings. The single interactive accent |

**Colour discipline (hard rules):**
- Saturated colour appears only inside glass screens (lamp frames, spectrum, VFD digits)
  or in the six sanctioned roles above. Chrome never competes with the lamps.
- `--live` means one thing: signal exists right now. It never labels, borders or brands.
- `--deck-a…d` exist because the signal chain genuinely has four ordered stages
  (audio → beat → engine → bridge). They may not colour anything unordered.
- One accent. If two elements on a face both glow orange, one of them is lying.

## 3. Type — three voices, system stacks only

The box runs on a NAS with no internet. **No webfonts, no font files, ever.**

| Voice | Stack | Used for | Setting |
|---|---|---|---|
| Silkscreen | `"Bahnschrift","DIN Alternate","Avenir Next Condensed","Liberation Sans Narrow","Arial Narrow",system-ui` | legends, labels, keys, deck caps | 10–12 px · 600 · UPPERCASE · tracking `.10–.26em` |
| Body | `"Inter","Segoe UI",system-ui` | notes, descriptions | 12.5–13.5 px / 1.5 · sentence case |
| Counter | `ui-monospace,"Cascadia Mono","JetBrains Mono",Consolas` | every number, address, status readout | tabular-nums; 24 px in LCDs, 11–13 px inline |

Rules: anything printed on metal in the silkscreen voice is uppercase and tracked.
Anything numeric is mono. Body text is never uppercase. No italics anywhere — hardware
doesn't slant.

## 4. Surfaces — how each material is built

**Chassis** — flat `--chassis`; one radial glow at the very top of the page
(`radial-gradient` white 3–5%) and nothing else.

**Rail** (module title bar) — `linear-gradient(var(--rail-hi),var(--rail-lo))`,
`border-bottom:1px solid #000`, `inset 0 1px 0 rgba(255,255,255,.07)`. Contains: the
legend, optional live mono readouts, optional one toggle. Never inputs, never buttons.

**Faceplate** — the aluminum recipe, exactly:

```css
background:
  repeating-linear-gradient(180deg, rgba(255,255,255,.14) 0 1px, transparent 1px 4px),
  linear-gradient(180deg, var(--alu-hi), var(--alu) 14%, var(--alu) 86%, var(--alu-lo));
box-shadow: inset 0 1px 0 rgba(255,255,255,.8), inset 0 -1px 0 rgba(0,0,0,.18);
```

The 4 px repeating line is the brushing. Do not make it stronger; at arm's length it
should read as texture, not stripes.

**Glass screen** — recessed cut:

```css
background: var(--screen); border: 1px solid #000; border-radius: 5px;
box-shadow: inset 0 2px 8px rgba(0,0,0,.75),
            inset 0 0 0 1px rgba(255,255,255,.04),
            0 1px 0 rgba(255,255,255,.55);   /* the machined lip below the cut */
```

That last outer highlight is what makes a hole look drilled into metal instead of
painted on. Every recessed thing (screens, wells, fader slots, `.val` windows) carries
it.

**The bevel law:** on aluminum, light comes from above. Raised = light top edge, dark
bottom. Recessed = dark inner top, light outer bottom. Never both on one element.

## 5. Components

**Module** — `rail` + `plate`, radius 8 px, `border:1px solid #000`, drop shadow
`0 10px 24px rgba(0,0,0,.45)`. Modules never nest.

**Key (button)** — dark soft-touch key on metal: gradient `#3D434A→#26292D`, silkscreen
voice, radius 4 px, machined-lip highlight. `:active` goes flat and inset. `.primary` is
the backlit orange key (`#F7A945→#DE7A1D`, dark text, soft `--accent` glow) — **at most
one lit primary key per module.**

**Well (text input / select)** — glass recipe at input scale, mono text, placeholder
`#5C646C`.

**Fader (range)** — 7 px recessed slot + 26×17 px brushed-metal cap with a single dark
center line. No round thumbs — round thumbs are a web control, not a fader.

**LED** — 8 px dot, off = near-black recessed socket; on = `--live` fill +
`0 0 8px` glow; warning = `--hot`. An LED label sits right of the dot, silkscreen voice,
lit text `#E8ECEF` / unlit `--rail-dim`.

**Deck cap** — 18 px rounded square, deck colour, dark letter, machined lip. Only A–D,
only in the signal chain.

**LCD** — glass screen, ~52 px tall, one `--vfd` mono figure with its small unit label.
One number per LCD. Amber digits get `text-shadow: 0 0 10px rgba(255,184,104,.35)` and
that is the only text glow on the page.

**Pad (palette swatch)** — dark chip on glass logic: colour bars 26 px tall, caption
under. Selected = 2 px inset `--accent` ring + glow (a lit cue pad). The rotation `ROT`
tick is a tiny `--deck-b` chip. A disabled board dims to 45% + grayscale — hardware you
can see but not touch.

**Light tile** — face (peak-held lamp colour, 56 px) → 20 px trail canvas
(`image-rendering:pixelated` — the trail is data, let the pixels show) → name in
silkscreen light grey → 4 px `--live` level bar. The trail strip is the product's
signature; nothing may ever be drawn over it.

## 6. Layout & spacing

- Grid gap and page rhythm: **16 px** between modules, 14–16 px plate padding,
  7 px row rhythm. Spacing scale: 4 / 8 / 10 / 14 / 16 — nothing off-scale.
- Max width 1180 px, centred. Desktop (≥940 px): sources module 7fr beside a 5fr stack
  of bridge + calibration; wall, chain, settings, senders full width. Below 940 px:
  one column, order unchanged.
- Signal chain: 4-up ≥760 px with `▸` connectors, 2×2 below (connectors hidden).
- Rows: 148 px label column, wraps to full width ≤560 px. Wall tiles:
  `minmax(148px,1fr)` auto-fill.
- Compact by default: body 13.5 px, no section eats vertical space to look important.

## 7. Motion & accessibility (quality floor)

- Transitions: 60–80 ms linear on meters/lamp faces, ~150 ms on hovers. Nothing bounces,
  nothing eases dramatically — hardware snaps.
- `prefers-reduced-motion`: all CSS transitions off; the JS already freezes the trail
  scroll while colours keep updating. Preserve both.
- Focus: 2 px `--accent` outline, offset 2 px, on every focusable element, both surfaces.
- Contrast: `--ink` on `--alu` ≈ 9:1, `--ink-soft` ≈ 5:1, `--read` on `--screen` ≈ 13:1.
  Do not lighten inks or dim reads below these pairs.
- No external requests of any kind. Icons are inline `<symbol>`s, fonts are system.

## 8. Footer & brand marks

- Footer is chassis material. Three brand sets under 9.5 px eyebrows:
  **Player plug-ins** (Winamp · foobar2000 · AIMP), **Sound capture** (Windows · macOS),
  **Protocols** (snapcast · dlna · squeezebox).
- Marks are monochrome, `currentColor`, 20 px, resting `--rail-dim`, hover `#E8ECEF`.
  Never the vendors' brand colours — colour belongs to the lamps.
- Protocol chips are placeholders with stable ids (`#proto-snapcast`, `#proto-dlna`,
  `#proto-squeezebox`): drop a 14×14 monochrome SVG inside the chip's `.proto-slot`
  when the icons are ready; the empty recessed square disappears automatically.
- The copyright block keeps the devspark / forum.qnap.net.pl / myqnap.org lines and the
  non-affiliation notice. Legal text: 11.5 px, `#5C646C`.

## 9. Do / Don't

| Do | Don't |
|---|---|
| Put every number in mono, every label in silkscreen caps | Mix voices, or uppercase body text |
| Cut a glass screen for anything colourful or live | Put saturated colour on aluminum or rails |
| One backlit primary key per module | Two glowing controls competing on one face |
| Recess inputs, raise keys, follow the bevel law | Flat borders, outline-only "ghost" buttons |
| Keep `--live` for signal-present only | Green as a brand or success colour |
| Extend by adding a module (rail + plate) | Invent a fourth surface material |
| System font stacks | Webfonts, icon fonts, any external asset |

## 10. Contract with the JavaScript

The script is untouched and depends on these staying exactly as they are: every element
`id` in the current markup; classes `lamp on warn`, `row`, `grow`, `val`, `legend`,
`note`, `fieldname`, `toggle`, `board off`, `swatches`, `sw`, `bars`, `t`, `tick`,
`tile face lvl nm foot`, `empty`, `primary`; the CSS variable **`--legend`** (the script
writes `color:var(--legend)` inline); and `#spec` receiving 17 bare `<i>` children.
Renaming any of these is a breaking change to the app, not a style decision.
