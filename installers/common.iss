; Shared definitions for every TuneThatHue installer.
; Included by tth-soundrecorder.iss / tth-winamp.iss / tth-foobar.iss.

#define TTHVersion      "1.0.0"
#define TTHPublisher    "Silas Mariusz Grzybacz - devspark.pl"
#define TTHCopyright    "(c) 2025-2026 Silas Mariusz Grzybacz"
#define TTHUrl          "https://devspark.pl"
#define TTHSupportUrl   "https://forum.qnap.net.pl"
#define TTHUpdatesUrl   "https://myqnap.org"
#define TTHIcon         "..\resources\tth.ico"

; Every installer repeats this. NOTE: no apostrophes - this text is pasted into
; Pascal single-quoted strings in the [Code] sections, where an apostrophe would
; terminate the string and break compilation.
#define TTHDaemonNote  "TuneThatHue sends audio to the TuneThatHue daemon (a QNAP NAS, a Raspberry Pi, or any PC running it). Set the daemon IP and port in the settings - the default is 127.0.0.1:6980 when the daemon runs on this machine."
