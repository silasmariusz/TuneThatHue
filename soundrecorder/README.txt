TuneThatHue SoundRecorder
=========================

Captures what this PC is playing and sends it to the TuneThatHue daemon, which
turns it into light effects on Philips Hue.

(c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl
published: forum.qnap.net.pl   qnap app repo: myqnap.org


HOW TO USE
----------
1. Start it. It sits in the system tray (no window).
2. Double-click the tray icon to open the settings.
3. Set "Daemon IP" and "Port" to your TuneThatHue daemon
   (127.0.0.1 : 6980 if the daemon runs on this same PC; otherwise the IP of
   your QNAP NAS / Raspberry Pi / other machine).
4. Click "Test connection" - it should say "OK - daemon replied".
5. Pick what to capture (see below), click OK, and play something.
   The "Throughput" line shows data leaving the app.


WHAT IT CAN CAPTURE
-------------------
Default output (everything you hear)
    The normal choice. Captures the mix of everything playing on your default
    playback device - any player, any browser, any game. DirectSound, XAudio2
    and DirectX audio are all included, because Windows mixes them together
    before they reach the speakers.

Specific output device
    The same, but on a device you choose: a second sound card, an HDMI output,
    a virtual audio cable.

Input device (Stereo Mix / line-in / mic)
    Records from a recording device instead - "Stereo Mix"/"What U Hear" on
    sound cards that offer it, a line-in, or a microphone.

One application only
    Captures a single program (Windows 10 version 2004 / build 19041 and newer).
    Pick the app from the list; only its audio is sent. If the app is not
    running yet, the recorder waits for it and starts automatically.


KNOWN LIMIT - EXCLUSIVE MODE
----------------------------
Some programs (certain DJ software, ASIO-style setups, a few games) open the
sound device in "exclusive mode". That bypasses the Windows mixer completely, so
capturing that device records silence. Nothing can intercept it there. Use
"One application only" for that program, or send its audio to another device.


SETTINGS FILE
-------------
Settings are stored in TuneThatHue-SoundRecorder.ini next to the program.


NOTE
----
Sending audio is only half of the job: the daemon turns it into light. Beat
synchronisation is a separate feature of the daemon and is not provided by this
app.
