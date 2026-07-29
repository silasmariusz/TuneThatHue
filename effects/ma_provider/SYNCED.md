# Music Assistant provider files (reference copy)

Byte-identical copies of the rest of the `hue_entertainment` provider. They
import Music Assistant, so they are NOT importable here and the daemon never
loads them - they are here so you can read and edit the whole provider in one
place while writing effects.

The runnable engine lives in `../hue_fx/`.

Sync both ways with `tools/sync_effects.py` (`--pull` / `--push`); never edit
a copy by hand on one side only.
