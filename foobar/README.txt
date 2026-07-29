TuneThatHue - foobar2000 component (foo_tunethathue.dll)
========================================================

Sends what foobar2000 is playing to the TuneThatHue daemon, which turns it into
light effects on Philips Hue. Playback is not altered - the component only
copies the audio to the network.

(c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
published: forum.qnap.net.pl   qnap app repo: myqnap.org

Requires 64-bit foobar2000 2.x.


INSTALL
-------
Easiest: double-click foo_tunethathue.fb2k-component - foobar2000 installs it
and asks to restart. (Or: File > Preferences > Components > Install...)


HOW TO USE
----------
1. File > Preferences > Playback > DSP Manager.
2. Move "TuneThatHue (send audio to daemon)" from "Available DSPs" into the
   "Active DSPs" list on the left. A DSP only runs while it is in that list.
3. With it selected, click "Configure selected".
4. Set "Daemon IP" and "Port" to your TuneThatHue daemon (127.0.0.1 : 6980 if
   the daemon runs on this same PC; otherwise the IP of your QNAP NAS /
   Raspberry Pi / other machine).
5. Click "Test connection" - it should say "OK - daemon replied".
6. Click OK, then play something.

To stop sending, remove it from the Active DSPs list.


WANT EVERYTHING, NOT JUST FOOBAR2000?
-------------------------------------
TuneThatHue SoundRecorder captures all system audio (or one chosen app) without
needing a plugin per player.


NOTE
----
Sending audio is only half of the job: the daemon turns it into light. Beat
synchronisation is a separate feature of the daemon and is not provided by this
component.
