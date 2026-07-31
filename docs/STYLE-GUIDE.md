# Panel style guide

The page has to look like equipment. Two materials, and the rule that follows from them:

**The chassis is dark.** Near-black, dark grey panels, thin borders, small condensed
labels. This is Traktor's frame and the numbers below are sampled from it.

**The lights are silver.** Each light gets a brushed metal deck, the way the front of a
cassette player or a 1970s amplifier looks. Silver against near-black is the contrast the
whole page is built on, and it is what makes a wall of lights read as a rack of machines.

Nothing else is silver. If a new element is not a light, it belongs to the chassis.

## Colour

    --chrome        #1C1C1C   the page
    --panel         #2C2E2E   a card face
    --panel-hi      #383B3B   a raised strip inside a card
    --inset         #141616   a recessed window, where light or audio shows
    --rule          #454848   hairlines
    --rule-dark     #171919   the dark half of a bevel
    --legend        #8A9090   silkscreen labels
    --read          #E6E9E9   readouts

    --silver-hi     #EDEEEE   deck highlight, top edge
    --silver        #D3D5D5   deck face
    --silver-lo     #A9ACAC   deck shadow, bottom edge
    --engrave       #4A4D4D   text engraved into a deck

    --live          #C6E33C   playing, signal present (Traktor's play green)
    --blue          #3C9AD9   synced, connected (Traktor's sync blue)
    --amber         #E2A032   VU upper range
    --red           #D8452F   VU peak

`--live` and `--blue` mean state, never decoration. Amber and red appear only at the top
of a meter.

## Material recipes

A **deck** (a light) is a silver panel:

    background: linear-gradient(180deg, --silver-hi 0%, --silver 45%, --silver-lo 100%)
    border: 1px solid #8E9191
    border-radius: 3px
    box-shadow: inset 0 1px 0 #FFF, inset 0 -1px 0 rgba(0,0,0,.25), 0 2px 5px rgba(0,0,0,.5)

Brushed texture is a repeating 2px linear-gradient at 1% opacity. Any more and it buzzes.

A **window** (where colour or audio shows) is recessed:

    background: --inset
    box-shadow: inset 0 2px 4px rgba(0,0,0,.8), inset 0 -1px 0 rgba(255,255,255,.06)

A **chassis card**:

    background: linear-gradient(180deg, #313434, --panel)
    border: 1px solid --rule-dark
    box-shadow: inset 0 1px 0 rgba(255,255,255,.06)

Buttons are the same recipe at a smaller radius. A pressed button loses the top
highlight and gains an inner shadow, so it reads as pushed in rather than tinted.

## Type

    Labels        condensed, uppercase, 10-11px, letter-spacing .14em
    Readouts      monospace, tabular figures
    Body          system sans, 13px

A deck's label is engraved: `--engrave` text with a 1px white bottom shadow. Never white
text on silver.

## Meters

**VU is a needle.** An arc scale, a pivot, and a needle that swings. Ballistics are the
point: about 300 ms to settle, so it moves like a meter and not like a bar graph. The
scale is dark on silver with a red zone in the last fifth.

**LEDs are a row.** Small, round, dark when idle, lit through green then amber then red.
Two red at the top, never more. They glow with a small coloured shadow rather than
changing size.

## Layout

The page is one column of cards, in the order of the signal: the lights first, then what
feeds them, then the settings.

Compact is a requirement, not a preference. Padding inside a card is 10-14px, never more.
Gaps between cards are 12px. Nothing gets a heading if a label will do.

**Mobile.** Below 640px: decks go to one column, controls stack under their labels, and
the meter shrinks but stays. Nothing hides behind a hover, because a phone has no hover.

## Words

Shortest thing that is still true. A control is named by what it does. Explanations that
are only there to describe a limitation belong in the README, not on the page.
