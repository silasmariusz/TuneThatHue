# Work in progress

Everything below is either done or being done now. Each line says what "finished" means,
so there is no argument later about whether it was.

## A. The panel, third pass

| | What | Done when |
| --- | --- | --- |
| A1 | Chain layout: audio across the top, meters in the middle, beat + engine + bridge along the bottom | The three counters sit in one row and audio spans the full width |
| A2 | Analog faces bigger; LED ladders matched to their height | The pair reads as one instrument, not two parts |
| A3 | Every paragraph replaced by a `?` next to its window | No body copy left on the page; hovering a `?` shows the card |
| A4 | Displays refresh fast enough to look alive | Status polled 5×/s, not once a second |
| A5 | Aluminium only on the signal chain; every other module dark | One metal plate on the page |
| A6 | Senders section removed | The footer is the only place to get them |
| A7 | Footer carries plug-ins, capture apps, protocols and platforms, all linking out | Every mark is a link |
| A8 | The stray `▸` between chain links removed | It belonged to the four-across layout and pointed at nothing |

## B. The four that were left open

| | What | Done when |
| --- | --- | --- |
| B1 | Windows daemon installer compiled | A setup .exe exists and carries a Windows ffmpeg |
| B2 | Linux install script run on a real Ubuntu 24.04 | Service starts, `tunethathue status` answers, panel responds |
| B3 | macOS install script | Honest note: no Mac here to run it on |
| B4 | Docker image, multi-architecture | Image builds and runs; publishing needs the account |
| B5 | WLED output | A strip lights from the same frames the bridge gets |

## Notes worth keeping

**`.link:not(:last-child)::after`** drew a small `▸` after every chain link except the
last, to suggest a signal flowing left to right through four columns. The layout is no
longer four columns, so it pointed at nothing and appeared in odd places. Removed rather
than reworked: the chain reads as a sequence from its own arrangement now.

**Refresh rate.** A meter updated once a second is not a meter. The status poll runs at
five times a second and the needle eases over 260 ms, which is roughly how a real VU
behaves. The counters are cheap to redraw, so they ride along.
