# What is being built next

Three pieces of work, in the order they get done. Each one is finished and tested before
the next starts.

## 1. Run it on a desktop, not only on a NAS

The daemon is Python and already runs anywhere. What is missing is everything around it:
an installer, a service that starts at boot, and a way to turn it on and off without a
terminal.

**Windows**
- Installer built with Inno Setup, the same tool the sender installers use.
- Runs as a Windows service so it starts at boot with no one logged in.
- A tray icon to start, stop and open the panel. Written in C, like the Sound Capture
  tray already in this repo.

**macOS**
- A `.pkg` installer.
- A launchd job, so it starts at login and restarts if it dies.
- A menu-bar item with the same three commands.

**Linux (Ubuntu 24.04, Debian)**
- A `.deb`.
- A systemd unit, enabled at install.
- A tray icon through the standard AppIndicator, and a desktop entry for the panel.

All three install the same thing: the daemon, the bundled ffmpeg for that architecture,
and a control command (`tunethathue start|stop|status|panel`). The tray is a front end
for that command, so anything the tray does can also be done from a terminal or a script.

## 2. A plug-in for AIMP

AIMP has a documented C++ SDK with a DSP interface, which is the same shape as the Winamp
and foobar2000 plug-ins already here: take the audio as it plays, copy it out over the
network, leave the sound untouched. The wire format is the one the other two senders
already use, so nothing on the daemon side changes.

Windows only, because AIMP is.

## 3. The panel, rebuilt

The current panel works and is plain. The next one should look like equipment.

**Direction: Traktor Pro 4, with the lights as tape decks.**

Native Instruments' Traktor is the reference for the frame: near-black background, dark
grey panels, thin borders, small condensed labels, controls that look machined rather
than drawn. Everything in the panel keeps that.

The exception is the light wall. Each light gets a **light grey panel**, the way the
front of a cassette deck or a 1970s amplifier looks: brushed metal, a recessed window,
a small label. Silver against near-black is the contrast the whole page is built on, and
it is what makes a room full of lights read as a rack of machines.

**What that means in practice**
- A **VU meter per light** with a moving needle, not a bar. Small, warm, unhurried.
- Level LEDs that glow rather than shout: amber to red at the top, dark when idle.
- Real bevels and shadows, so a panel looks pressed out of metal instead of filled with
  a colour.
- Type stays small, condensed and quiet, as it is on a mixer's silkscreen.

**Compact and mobile**
The page has to fit a phone held in one hand while standing at the lights. That means
the light wall reflows to one column, the controls stay reachable with a thumb, and
nothing is hidden behind a hover.

**Text gets cut.** Every explanation on the page is shortened to what a person needs at
that moment. If a sentence is only there to explain a limitation, it moves to the README.

**Get the senders becomes icons.** Winamp, foobar2000, AIMP, and the Windows and macOS
capture apps, as logos in a row, each linking to its download. Same treatment later for
the protocol marks: Snapcast, DLNA, Squeezebox.

**Footer** carries the copyright, then those logos.

A written style guide comes with it, so the page can grow without drifting: colours,
type sizes, border and shadow recipes, spacing, and what a panel, a meter and a button
are made of.

## Branch and history

This work stays on `network-device` until the four inputs and the decoders are merged
into `main`, because that was the point of the branch. The redesign then continues on
top. Planning documents that describe finished work are deleted rather than kept: the
code and the README are the record.
