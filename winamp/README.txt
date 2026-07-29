TuneThatHue - Winamp plugin (dsp_tunethathue.dll)
=================================================

Sends what Winamp is playing to the TuneThatHue daemon, which turns it into
light effects on Philips Hue. Playback is not altered - the plugin only copies
the audio to the network.

(c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
published: forum.qnap.net.pl   qnap app repo: myqnap.org


HOW TO USE
----------
1. In Winamp: Options > Preferences > Plug-ins > DSP/Effect.
2. Select "TuneThatHue (send audio to daemon)".
3. Click "Configure" and set "Daemon IP" and "Port" to your TuneThatHue daemon
   (127.0.0.1 : 6980 if the daemon runs on this same PC; otherwise the IP of
   your QNAP NAS / Raspberry Pi / other machine).
4. Click "Test connection" - it should say "OK - daemon replied".
5. Play something. The "Throughput" line shows data leaving the plugin.

To stop sending, pick "(none)" in the DSP/Effect list.


MANUAL INSTALL
--------------
Copy dsp_tunethathue.dll into Winamp's Plugins folder, usually
    C:\Program Files (x86)\Winamp\Plugins
then restart Winamp.


SETTINGS FILE
-------------
Settings are stored in dsp_tunethathue.ini next to the DLL.


WANT EVERYTHING, NOT JUST WINAMP?
---------------------------------
TuneThatHue SoundRecorder captures all system audio (or one chosen app) without
needing a plugin per player.


NOTE
----
Sending audio is only half of the job: the daemon turns it into light. Beat
synchronisation is a separate feature of the daemon and is not provided by this
plugin.
