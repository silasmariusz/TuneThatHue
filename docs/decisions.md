# Decisions

Each entry records what was decided and why. The code is the source of truth for how it works;
`docs/qnap-service.md` describes the NAS package as it is.

## D1 - The NAS package starts only when Enable is TRUE, and App Center's Open always reaches the panel (2026-10-22, 0.9.10)

The owner, 2026-10-22: "pasuje naprawić skrypt start/stop i sprawdzić, żeby appka się
uruchamiała tylko wtedy, jak Enable jest set na TRUE w qpkg.conf; poza tym nie odpala się."
Reproduced on the test NAS units (QTS 6.1 and QuTS hero 6.1) with the published 0.9.8.

1. **Found: why it "does not open".** The daemon starts, but after App Center's switch is
   turned on (`qpkg_cli --enable`) QTS writes `ProxyPass "/TuneThatHue" "http://127.0.0.1:58091/"`
   into `/etc/app_proxy.conf` above the script's own block. Apache takes the first matching
   rule, so `/TuneThatHue/api/status` reached the daemon as `//api/status`, and the daemon
   answered 404. App Center's Open (`/TuneThatHue`, redirected to `/TuneThatHue/`) showed
   "Not Found" until the next plain `start`. Seen on both units, with 0.9.7, 0.9.8 and 0.9.9.
2. **`Proxy_Path` is `/TuneThatHue/`.** With the slash, QTS's line maps `/TuneThatHue/x` to
   `http://127.0.0.1:58091/x`, the same as the script's block, so the order of the two does not
   matter, and App Center's Open goes straight to `/TuneThatHue/`. The script no longer
   deletes QTS's line (deleting it was a race QTS always won). It removes only the old
   non-slash form an upgrade leaves behind. The block stays for the bare-path redirect, the
   forwarded headers, `ProxyPassReverse` and firmware that writes no line.
3. **Enable decides, everywhere.** `start` already refused a switched-off package, but the cron
   watchdog (`daemon.sh watchdog`, every two minutes) and `restart` started the daemon with
   `Enable = FALSE` (shown on the QTS unit); a watchdog line left by a stop that never ran (a
   power cut) would start it after a reboot although App Center shows it off. Now `start`, `restart` and the
   watchdog all read `getcfg TuneThatHue Enable -u -d FALSE`; anything but TRUE means exit 1
   with the reason and nothing made. The watchdog also needs `Status = complete` and the run
   marker `/var/run/tunethathue.run`, which `start` writes and `stop` removes before it ends
   the daemon. A lock folder keeps a watchdog run and a `stop` from overlapping.
4. **Start makes, stop takes away.** The marker, the route block and the cron line are made by
   `start` and removed by `stop`, and once more by `PKG_POST_REMOVE` with literal paths. The
   package makes no link in `/usr/bin`; QTS's own `/etc/init.d/tunethathue.sh` is the
   command for a person.
5. **Stop ends only this installation's daemon,** found by `/proc/<pid>/exe` being the same
   file as the bundled `python3` (`-ef`, true for both names QuTS hero gives one folder), not
   by `ps | grep tth_phase2.py`, which also matched any other process naming that file.
6. **One script.** `etc/tunethathue/daemon.sh` is gone; `tunethathue.sh` has the
   `watchdog` verb. `pkg_post_install` deletes the old file, the old cron line and the
   `app.new/` and `app.prev/` copies 0.9.x shipped by mistake.
7. **Files are private.** QTS runs package scripts with umask 0000, and 0.9.x left
   `config/` (the bridge's application key and client key) at 0777/0664 and the log at 0666.
   The script works with umask 077 and closes `config/`, `var/` and their files at every
   start; `pkg_post_install` does it too. The old `stop` also replaced root's
   `/etc/config/crontab` with a file made under umask 0000 (0666, writable by every user);
   every crontab edit now runs `chmod go-w` on it (the firmware's mode is 0644). No file goes
   through the NAS's `/tmp`.
8. **The log has a cap.** The daemon writes a stats line a second; the log reached 321 MB on
   one NAS and 231 MB on another. At 8 MiB the last 1 MiB moves to `tunethathue.log.1` and
   the log is emptied in place (the daemon appends, so it keeps writing). The first start of
   0.9.10 cuts an existing large log the same way.

Checked on the two test units (QTS 6.1, QuTS hero 6.1) before the release: upgrades from 0.9.7, 0.9.8 and 0.9.9 keep the
settings; App Center's switch off and on twice, then stop and start, each end with
`/TuneThatHue/` answering 200; start, restart, a boot-time start and the watchdog refuse
`Enable = FALSE` (also `false` and an empty value) and make nothing; the watchdog restarts a
killed daemon, and a real cron run did it within 80 seconds; a start under umask 0000 leaves
`config/` 0700 and `hue-box.toml` 0600; a reinstall over itself keeps the settings; an
uninstall leaves no file, link, icon, cron line or proxy line behind, and on the QTS unit
`/etc/app_proxy.conf` and the crontab equal their copies from before the first install.
