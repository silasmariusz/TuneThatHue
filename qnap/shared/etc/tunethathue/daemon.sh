#!/bin/sh
# TuneThatHue daemon controller (POSIX-sh, QNAP busybox-friendly).
# Adapted from the sdlv daemon controller pattern.
#
# Actions:
#   start      launch the daemon (no watchdog)
#   stop       stop the daemon
#   restart
#   status
#   watchdog   called from cron: (re)start the daemon if it is not healthy
#
# The daemon is the TuneThatHue Python app, run under the bundled portable
# CPython for this architecture. Watchdog supervision is done via a cron line
# installed by tunethathue.sh (start) and removed on stop.

QPKG_NAME="TuneThatHue"
QPKG_ROOT="${QPKG_ROOT:-/opt/${QPKG_NAME}}"
APP="$QPKG_ROOT/app"
RUNTIME_DIR="$QPKG_ROOT/runtime"
CONF="$QPKG_ROOT/config/hue-box.toml"
VAR="$QPKG_ROOT/var"
PIDFILE="$VAR/run/tunethathue.pid"
LOG="$VAR/log/tunethathue.log"
WEBUI_PORT="${TTH_WEBUI_PORT:-58091}"
VBAN_PORT="${TTH_VBAN_PORT:-6980}"

arch_detect() {
    case "$(uname -m)" in
        x86_64)          echo x86_64 ;;
        aarch64|arm64)   echo aarch64 ;;
        armv7l|armv7|armhf) echo armv7 ;;
        *)               uname -m ;;
    esac
}

ensure_dirs() {
    mkdir -p "$VAR/run" "$VAR/log" "$QPKG_ROOT/config"
    # Seed the config from the example on first run so the WebUI has something.
    [ -f "$CONF" ] || cp "$APP/config/hue-box.example.toml" "$CONF" 2>/dev/null || true
}

python_bin() {
    echo "$RUNTIME_DIR/python-$(arch_detect)/python/bin/python3"
}

daemon_alive() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}

find_pids_by_name() {
    ps 2>/dev/null | grep -F "$1" | grep -v grep | awk '{print $1}'
}

start_daemon() {
    if daemon_alive; then
        echo "TuneThatHue: already running (pid $(cat "$PIDFILE"))"
        return 0
    fi
    ensure_dirs
    PY="$(python_bin)"
    if [ ! -x "$PY" ]; then
        echo "TuneThatHue: runtime not found for $(arch_detect): $PY" >&2
        return 1
    fi

    # Detach without nohup/setsid (busybox compatible): subshell ignores SIGHUP,
    # redirects fds, cd / so a later reinstall of the QPKG dir can't strand cwd.
    (
        trap '' HUP
        cd / 2>/dev/null || true
        exec </dev/null >>"$LOG" 2>&1
        "$PY" "$APP/python/tth_phase2.py" \
            --output none --config "$CONF" \
            --port "$VBAN_PORT" --webui-port "$WEBUI_PORT" &
        echo $! > "$PIDFILE"
    ) &

    i=0
    while [ $i -lt 10 ]; do
        daemon_alive && break
        sleep 1
        i=$((i + 1))
    done
    if daemon_alive; then
        echo "TuneThatHue: started pid $(cat "$PIDFILE") (WebUI on :$WEBUI_PORT)"
        return 0
    fi
    echo "TuneThatHue: failed to start; see $LOG" >&2
    return 1
}

stop_daemon() {
    pid=""
    [ -f "$PIDFILE" ] && pid=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null
        i=0
        while [ $i -lt 5 ] && kill -0 "$pid" 2>/dev/null; do
            sleep 1
            i=$((i + 1))
        done
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
    fi
    # Sweep by name too: the pidfile can be stale after a watchdog respawn/crash.
    # "tth_phase2.py" matches the daemon; it never matches this controller.
    for p in $(find_pids_by_name 'tth_phase2.py'); do kill -TERM "$p" 2>/dev/null || true; done
    sleep 1
    for p in $(find_pids_by_name 'tth_phase2.py'); do kill -KILL "$p" 2>/dev/null || true; done
    rm -f "$PIDFILE"
    echo "TuneThatHue: stopped"
}

case "$1" in
    start)    start_daemon ;;
    stop)     stop_daemon ;;
    restart)  stop_daemon; start_daemon ;;
    status)   daemon_alive && echo "running (pid $(cat "$PIDFILE"))" || { echo "stopped"; exit 1; } ;;
    watchdog) daemon_alive || { echo "$(date) unhealthy - restarting" >> "$LOG"; start_daemon; } ;;
    *)        echo "usage: $0 {start|stop|restart|status|watchdog}" >&2; exit 2 ;;
esac
