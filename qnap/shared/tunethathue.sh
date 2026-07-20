#!/bin/sh
# TuneThatHue QPKG service program (QPKG_SERVICE_PROGRAM).
# Handles the QNAP start/stop lifecycle, delegates the daemon itself to the
# controller in etc/tunethathue/daemon.sh, and installs a cron-based watchdog.
#
# TuneThatHue (c) 2025-2026 Silas Mariusz Grzybacz - devspark.pl

QPKG_NAME="TuneThatHue"
CONF_QPKG="/etc/config/qpkg.conf"
QPKG_ROOT="$(/sbin/getcfg "$QPKG_NAME" Install_Path -f "$CONF_QPKG" 2>/dev/null)"
[ -n "$QPKG_ROOT" ] || QPKG_ROOT="/opt/${QPKG_NAME}"
export QPKG_ROOT

CTL="$QPKG_ROOT/etc/tunethathue/daemon.sh"

# ── cron-based watchdog ──────────────────────────────────────────────────────
# A single crontab line resurrects the daemon if it dies. It survives reboots
# (QNAP restores /etc/config/crontab), so this is the supervision mechanism.
# `stop` removes it so the daemon actually stays stopped.
WD_MARK="# TuneThatHue-watchdog"
WD_LINE="*/2 * * * * /bin/sh ${CTL} watchdog >/dev/null 2>&1 ${WD_MARK}"

watchdog_cron_add() {
    [ -f /etc/config/crontab ] || return 0
    if ! grep -qF "$WD_MARK" /etc/config/crontab 2>/dev/null; then
        printf '%s\n' "$WD_LINE" >> /etc/config/crontab
        [ -x /usr/bin/crontab ] && /usr/bin/crontab /etc/config/crontab 2>/dev/null
        echo "TuneThatHue: watchdog cron installed (*/2)"
    fi
}

watchdog_cron_remove() {
    [ -f /etc/config/crontab ] || return 0
    if grep -qF "$WD_MARK" /etc/config/crontab 2>/dev/null; then
        grep -vF "$WD_MARK" /etc/config/crontab > "/tmp/tth_ct.$$" 2>/dev/null \
            && mv "/tmp/tth_ct.$$" /etc/config/crontab
        [ -x /usr/bin/crontab ] && /usr/bin/crontab /etc/config/crontab 2>/dev/null
        echo "TuneThatHue: watchdog cron removed"
    fi
}

case "$1" in
  start)
    ENABLED=$(/sbin/getcfg "$QPKG_NAME" Enable -u -d FALSE -f "$CONF_QPKG" 2>/dev/null)
    [ "$ENABLED" = "TRUE" ] || { echo "$QPKG_NAME is disabled."; exit 1; }
    # The payload is mirrored from a repo that may carry no exec bit.
    chmod 0755 "$QPKG_ROOT/tunethathue.sh" "$CTL" 2>/dev/null
    chmod 0755 "$QPKG_ROOT"/runtime/python-*/python/bin/python3* 2>/dev/null
    "$CTL" start
    watchdog_cron_add
    ;;
  stop)
    watchdog_cron_remove
    "$CTL" stop
    ;;
  restart)
    watchdog_cron_remove
    "$CTL" restart
    watchdog_cron_add
    ;;
  status)
    "$CTL" status
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
