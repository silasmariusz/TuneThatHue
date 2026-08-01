# Beat Grid Accuracy — Test & Improvement Plan

*2026-07-31. Problem: the 1/4 2/4 3/4 4/4 beat counter visibly skips and jumps, and beat-driven
effects (Pulse/Club fire, downbeat blooms) sometimes miss or double-fire. Seen in both
TuneThatHue standalone and Music Assistant. Test corpus prepared in `E:\mp3\`.*

---

## 1. Who makes the beats (two different pipelines)

| Input | Beat source | Quality expectation |
|---|---|---|
| MA → Sendspin visualizer | **Server-side offline neural model** (Beat This, torch) + pure-numpy DBN post ([server/.../smart_fades/dbn_postprocessor.py](../../server/music_assistant/providers/smart_fades/dbn_postprocessor.py)), hydrated as `BeatTiming` in [player.py:1870-1893](../../server/music_assistant/providers/sendspin/player.py) | Near-perfect on 4/4 EDM |
| Snapcast / Squeezebox / DLNA / VBAN (standalone) | **Our live DSP tracker** [python/beat_tracker.py](../python/beat_tracker.py) (spectral flux → ACF+comb → phase fit) | Classic-DSP grade; the suspect |

Both pipelines then share the same consumption layer (verbatim MA copy):
`analyzer.push_beats()` → `_ScheduledBeat` deque → `structure.note_beat()` → the **displayed**
counter `structure.beat_in_bar(now_us)`.

So "czy to nasza kwestia czy MA" decomposes into three independently testable layers:
**(A)** beat *source* accuracy, **(B)** schedule *consumption* (analyzer), **(C)** counter
*display* (structure + webui). A and B/C can each produce visible skipping on their own.

## 2. Concrete suspects found by code reading

- **S1 — duplicate re-emission (live tracker, HIGH confidence bug).**
  `_emit()` hands beats up to 1.2 s ahead (`_LOOKAHEAD_S`). Every successful re-estimate
  (every 0.5 s while locked — the ≤4 % drift path always re-latches) calls `_update_phase()`,
  which **restarts emission from "now"** (`beat_tracker.py:297-300`), re-emitting the already
  scheduled ~1-3 beats on the refined grid at slightly shifted timestamps. Nothing dedups:
  not `tth_phase2.py:469`, not `hue_box_main.py:306`, not `analyzer.push_beats()`.
  Consequences: micro-segments in the analyzer (double pulse / skipped hold), the continuous
  `beat_in_bar` counter inflates (palette races), `structure._beat_count` inflates (dwell
  logic wrong), downbeat duplicates re-anchor the bar mid-bar. This alone reproduces
  "omija / przeskakuje".
- **S2 — downbeat flapping.** `_bar_offset` is recomputed on every estimate from an 8 s bass
  window with no hysteresis (`beat_tracker.py:288-295`). One noisy window flips which beat is
  "1" → the counter jumps by 1-3.
- **S3 — silence during breakdowns.** When ACF confidence decays below `_LOCK_THRESHOLD = 3.8`
  (quiet 8-16 bar breakdowns have no kick), `locked` goes false and emission stops entirely;
  on re-lock the grid re-anchors from scratch. Reads as "gubi takty przez kilka taktów".
- **S4 — extrapolated display counter.** `structure.beat_in_bar()` divides
  `(now - bar_anchor) / ibi_ema` (`structure.py:271-276`). A 1-2 % EMA error accumulates
  across the bar → the integer division occasionally yields 1,2,2,3 or 1,2,4,1 even with a
  *perfect* schedule sitting in `analyzer._beats`. `_beats_per_bar` is hard-coded 4.
  **This code is shared 1:1 with the MA provider — if MA shows the same jumpiness while its
  neural beats are known-good, this is the culprit there.**
- **S5 — tempo octave errors** on non-trance material (prior centred at 125 BPM, ±0.9 oct).
- **S6 — MA-source edge cases.** Beats absent (smart_fades not analysed yet / retry cap) drop
  the renderer into the onset-walk fallback, which *looks* like skipped beats; flow-stream
  anchoring offsets shift the whole grid.

## 3. Test corpus (`E:\mp3\`)

| Material | Ground truth | Exercises |
|---|---|---|
| `B_2022_Classic_Trance\*` (~38 tracks) | **BPM + Camelot key in filename** (`08A, 136 - …`) | steady 4/4 127-140 BPM, long breakdowns (S3), tempo accuracy |
| `Eelke_Kleijn-Connected…` (16 tracks) | album, melodic house/prog ~110-124 | softer kicks, octave prior stress (S5) |
| `house\*` | — | groove variety |
| `DAYS like NIGHTS 399` (2 h mix) | — | continuous stream, tempo transitions, re-latch behaviour (S1/S2) |
| `Eelke Kleijn - Transmission (…).flac` | — | lossless decode path |
| **Synthetic** (generated) | exact by construction | clicks 70-185 BPM, click+pink-noise, kick-bar loops, ±2 % tempo ramp, 8-bar silence/breakdown gap, offbeat-hat traps |

## 4. Test harness (Stage 0 — build first)

New `tools/beat_bench.py` (+ `tools/make_clicks.py`), venv-runnable, no daemon needed:

1. **Feed path fidelity**: decode with ffmpeg → int16 PCM → chunked into the tracker with the
   *same* timestamp math as `tth_phase2.on_chunk` (this is what makes results transferable).
2. **Log everything** as JSONL: every emitted beat (ts, is_downbeat), every re-latch
   (old/new period+phase), lock state timeline, BPM timeline.
3. **Reference grid**: `librosa.beat.beat_track` (or madmom if it installs) per track;
   filename BPM as tempo truth for the trance folder. For synthetic tracks the truth is exact.
4. **Metrics** (mir_eval-standard, implemented locally):
   - F-measure @ ±70 ms; continuity CMLt/AMLt;
   - tempo accuracy vs filename BPM, with explicit half/double-octave flagging;
   - downbeat F-measure;
   - **duplicate rate** (inter-beat interval < 0.5× median) — direct S1 detector;
   - **skip rate** (IBI > 1.5× median) and lock-coverage % — S3 detector;
   - `bar_offset` flip count per track — S2 detector.
5. **Counter-integrity replay** (the A-vs-B/C separator): synthesize a *perfect* `BeatTiming`
   list (and separately the tracker's real output), push through
   `HueAudioAnalyzer + StructureDetector`, sample `beat_in_bar` every 20 ms like the render
   loop, and assert the sequence is strictly 1,2,3,4,1,… **If the perfect grid still skips,
   the bug is the shared display layer (S4) → it is "ours" in both projects and fixable in
   both at once.**
6. Optional cross-check: dump `analysis.beats/downbeats` from MA (Muzyka add-on) for a few
   corpus tracks and score them with the same metrics — closes the "czy MA" question with data.

## 5. Improvements (ranked, each gated by the bench)

- **I1 — emission continuity (fixes S1).** Track an `_emitted_until` frontier; after any
  re-estimate resume from the first grid beat *beyond* the frontier, snapped to the new grid.
  Never re-emit. (Alternative belt-and-braces: dedup window in `push_beats` — but fixing the
  source is cleaner and keeps MA parity.)
- **I2 — grid continuity on re-latch.** Blend period (EMA) and snap the new phase to the
  nearest integer beat of the old grid, so refinements slide the grid instead of tearing it;
  a ±1-beat slip becomes impossible during steady playback.
- **I3 — downbeat hysteresis (fixes S2).** Adopt a new `_bar_offset` only after it wins
  3 consecutive estimates.
- **I4 — coast through breakdowns (fixes S3).** While confidence is below lock, keep
  extrapolating the last grid for up to N bars (e.g. 16) instead of going silent; drop out
  only after a hard timeout or a contradicting re-lock.
- **I5 — schedule-driven counter (fixes S4, benefits MA too).** Derive `beat_in_bar` from the
  actual scheduled beats (count from the last downbeat in the deque; the analyzer already
  knows `prior`), extrapolate only when the schedule runs dry. Port the same patch to the MA
  provider copy once proven.
- **I6 — bench-driven tuning.** Prior width vs the house corpus, `_LOCK_THRESHOLD`,
  superflux-style ODF (vibrato suppression), `_TIME_CORRECTION_FRAMES` per sample rate.
- **I7 — stretch: sliding-window DBN on the box.** MA's `dbn_postprocessor.py` is pure numpy
  (drop-in madmom replacement, no torch). Run it over live onset activations in a rolling
  window as an optional high-accuracy mode — the biggest possible jump, reusing already-vetted
  MA code.

## 6. Stages & exit criteria

| Stage | Work | Exit criteria |
|---|---|---|
| 0 | `beat_bench.py` + `make_clicks.py` + baseline report over full corpus | metrics table for ≥20 real tracks + synthetic suite; S1 duplicate rate measured |
| 1 | I1-I4 in `beat_tracker.py` | duplicate rate ≈ 0; skip rate < 1 % on locked spans; trance tempo accuracy ≥ 95 % (no octave errors); breakdown coast verified on synthetic gap test |
| 2 | I5 counter fix (TTH `structure.py`/webui, then MA copy) | perfect-grid replay yields strictly 1,2,3,4 for entire tracks; live check on Muzyka add-on |
| 3 | I6 tuning, optionally I7 | beat F-measure ≥ 0.90 trance / ≥ 0.80 house vs reference; DJ-mix transitions re-lock < 8 s without octave flips |

## 7. Results (2026-07-31 — executed)

Fidelity check first: `sync_effects.py --check` = 15/15 in sync, server HEAD == origin PR tip.
The copy was faithful; every symptom came from the code paths below.

Baseline (`runs/base-*`) vs after fixes (`runs/fixed-synth`, `runs/final-trance`), 180 s/track:

| Metric | Synthetic before | after | Trance corpus before | after |
|---|---|---|---|---|
| duplicate rate (S1) | 0.29 | **0.00** | 0.25 | **0.00** |
| beat F-measure ±70 ms | 0.45 | **0.91** (clicks ≈0.99) | — | — |
| downbeat moves (S2, total) | 854* | 33 | 5678* | 265 |
| tempo ok | 12/14 | 12/14 (two octave cases left) | 25/26 | 25/26 |
| counter on a PERFECT grid | breaks several times/track | **0 bad transitions** | — | — |

*before-numbers include index-renumbering artifacts; the after-metric counts musical moves only,
so the real improvement is larger than the ratio suggests.

Implemented: I1 (emission frontier + resume guard), I2 (period blend + phase snap onto the old
grid), I3 (absolute-frame downbeat identity + 3-vote hysteresis + clear-win margin `_BAR_MARGIN`),
I4 (16-bar coast window, `coasting` property), I5 (schedule-driven `beat_in_bar`/`bar_phase` in
structure.py, EMA as fallback only). Tests: `tests/` — 27 passing, the sync-fidelity guard runs
`sync_effects.py --check` on every pytest run. Mirrored to MA: commit `e65306d51` on
`hue-color-output` (PR #4853), explanation comment posted.

Live verification (DEV NAS 10.100.200.11, installed `TuneThatHue_0.9.3.qpkg`, signed, built on
.10): streamed a synthetic 128 BPM click over VBAN for 75 s while polling `/api/status` every
1.5 s. Result: bpm steady at 127.9, **154 beats emitted for 154 expected** (the pre-fix daemon
would have pushed ~460), `beat_in_bar` cycling 1..4 with `bar_phase` consistent with it in every
sample, lights rendering throughout.

### Round 2 (2026-08-01) — the melodic-house report from the field

The user played `12B, 123 - Ben Bohmer & Spencer Brown - Phases` and the grid misbehaved. Bench
on the file showed why: lock coverage 27 % (soft kick, long ambient sections) and latches onto
**165.4 = 123 x 4/3** (dotted-eighth delay pings; the kickless low band makes the tracker fall
back to full-band flux where the pings are the only periodicity) plus 137 (the intro arpeggio's
own pulse). The whole `MP3_selected_B_2023` folder showed the same class (Fatum -> 164,
Modeplex -> 152, Yotto -> 97 as final BPM).

Fixes (all measured, `runs/v094-*`):

- metrical candidates: the raw ACF winner is re-decided among x2, x1/2, x4/3, x3/4, x3/2, x2/3
  on INTERPOLATED comb scores (integer lags split fractional-period peaks - that is why 150 BPM
  read as 75) with the dance prior and a continuity bonus toward the track's established tempo;
- the continuity anchor is the MEDIAN of recent accepted periods (a wrong excursion cannot drag it);
- family snap: while the grid is alive, a hard latch at an exact (+/-2 %) metrical ratio of the
  anchor is converted to the base tempo - but only TOWARD the more probable dance tempo, so a
  wrong early anchor (an intro pulsing at 2/3 tempo) cannot capture the honest estimates
  (that asymmetry is what fixed Phases without breaking Jose Amnesia);
- lock hysteresis (enter 3.8 / exit 3.0, decay 0.85) and confidence erosion on rejected
  re-latches (a real tempo change now wins within seconds instead of never);
- new synthetics + tests: `delaytrap_123` (the 4/3 ping trap), tempo change mid-stream
  124 -> 90 (mixed playlists), vinyl wow +/-1.5 % @ 0.5 Hz (floating rips) - all green, and the
  150/174/185 octave xfails became ordinary passing tests.

Corpus results, final code: synthetics 15/15 tempo ok (F 0.98); trance 25/26 (the one miss is a
beatless ambient track); **B_2023 18/18**; house+inne+Eelke and the `sprawdzic_wazne` DJ sets:
zero duplicates, plausible tempos throughout (techno mixes 124-130, ISOS 133.8). Phases' delay
section now spends 0.5 s on 165 before snapping to 124.1 for the rest of the section.

Live on the dev NAS (0.9.4, user's own playback over VBAN): bpm pinned at 129.2, beats
incrementing at exactly the expected rate, counter and bar phase consistent in every sample.

Found while testing live: two simultaneous VBAN senders at different sample rates make
`_ensure_extractor` rebuild (and reset the tracker) on every alternation - the daemon needs
source arbitration (lock to one sender until it goes quiet). Filed as the next robustness item.

### Round 3 (2026-08-01) — DJ sets: stop going dark in breakdowns

Field report: the sets in `E:\mp3\sprawdzic_wazne` still lost the beat. `tools/set_report.py`
(new) shows where: emission gaps of 20-60 s wherever a breakdown outlived the 16-bar coast -
11 gaps in the first 300 s across the folder. Changes: coast raised to 64 bars; borderline
estimates that CONFIRM the living grid's tempo (within 8 %) are accepted as refinements from
strength 3.0 (phase stays fresh through soft sections) while only full-strength accepts feed
the tempo anchor and extend the coast (a wrong kickless-section grid cannot sustain itself on
its own weak echoes - that asymmetry was found the hard way: the first version poisoned the
anchor and let Phases' 165 section return); 5/4 joined the family-snap ratios (Yotto's rolling
riff parked the tracker at 4/5 tempo).

20-minute windows over all 27 sets: **total dark time 378 s across ~9 h** (was: hundreds of
seconds in the first five minutes alone), with the two largest remaining gaps (130 s in
Berghain pt3, 93 s in STEREOSENSE 02.05) sitting in genuinely beatless interludes where
stopping after two minutes of coasting is the intended behaviour. Set tempo timelines read
like the real sets (ISOS ramps 130.7 -> 136 -> 134). Regression: B_2023 18/18, trance 25/26,
synthetics 15/15, 33 tests green. Live on the dev NAS (0.9.5): the previously worst section
(Nu'kee 3:00-5:30, a 60 s dark hole) now streams beats continuously at 130 BPM.

Skill-registry search (background agent): no installable skill covers real-time beat
tracking; the meaningful upgrade path remains I7 (sliding-window DBN, MA's pure-numpy
postprocessor).

Still open (deliberate): octave-prior picks half tempo at 150/185 BPM on clicks (xfail'd,
tracked as I6); trance-corpus lock coverage 65 % counts `locked` only — emission continues
through coasting, so perceived coverage is higher; on real music the "1" still moves
occasionally where the accent is genuinely ambiguous (8 moves / 3 min on the worst track).

## 8. Notes

- No extra analysis skill needed: the loaded music-production skill's references already cover
  librosa/Beat This internals; the bench implements standard mir_eval metrics locally.
- Fix order matters: S1 (I1) first — it contaminates every downstream measurement, including
  the counter test.
- Keep MA 1:1 parity discipline: `beat_tracker.py` is TTH-only (free to change);
  `analyzer.py`/`structure.py` changes must be mirrored patches for the MA provider.
