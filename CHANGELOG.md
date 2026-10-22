# Changelog

Releases of every platform are on the [releases page](https://github.com/silasmariusz/TuneThatHue/releases).
This file lists the QNAP package's changes from 0.9.10 on.

## 0.9.10 (QNAP package, 2026-10-22)

- App Center's Open shows the panel after the app is switched on. Up to 0.9.9 the panel
  answered "Not Found" after every switch-on in App Center, until the next start. The panel
  path is now `/TuneThatHue/` (`Proxy_Path` in the package).
- The app runs only while it is switched on in App Center (Enable = TRUE). The watchdog and
  `restart` used to start it when it was switched off.
- Stopping ends only this package's own daemon.
- `config/` (the paired bridge's keys) and the log are readable by the administrator only.
  Root's crontab, which the old stop left writable by every user, is set back to 0644.
- The log is cut at 8 MiB; the last 1 MiB stays in `tunethathue.log.1`.
- Uninstalling leaves nothing behind outside the package folder.
- The app code is the same as 0.9.9's, apart from the import order in the Music Assistant
  provider's copy and the line endings of two logo files.
- The App Center icon is the Q with the hue wheel.

Details: [`docs/decisions.md`](docs/decisions.md) (D1), [`docs/qnap-service.md`](docs/qnap-service.md).
