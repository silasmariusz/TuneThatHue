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

# ── App Center / Main-menu icons ─────────────────────────────────────────────
# QDK copies the icons into the install root, but the panel serves them from
# /home/httpd/RSS/images/. Without this the tile keeps whatever placeholder was
# there, so refresh them on every start (cheap, and it repairs a firmware update
# that wiped the directory).
# Delete the destination FIRST. Until an app supplies its own icon the panel leaves a
# SYMLINK there pointing at the shared no_qpkg_icon_64.gif placeholder, and copying onto
# a symlink writes through it - which replaces the placeholder for every other app on
# the NAS. Verified the hard way on QuTS hero 6.0.
install_icons() {
    for _suffix in "" "_gray" "_80"; do
        _src="${QPKG_ROOT}/.qpkg_icon${_suffix}.gif"
        _dst="/home/httpd/RSS/images/${QPKG_NAME}${_suffix}.gif"
        [ -f "$_src" ] || continue
        rm -f "$_dst" 2>/dev/null
        cp -f "$_src" "$_dst" 2>/dev/null
    done
}
install_icons

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

# ── QTS panel-proxy route (/TuneThatHue -> our loopback WebUI) ───────────────
#   The panel apache Includes /etc/app_proxy.conf. `Use_Proxy=1` in qpkg.cfg makes
#   qpkgd emit only a bare, quoted one-liner:
#       ProxyPass "/TuneThatHue" "http://127.0.0.1:<port>/" retry=0
#   Apache maps a non-slash-terminated ProxyPass as that ONE exact URL, so the page
#   loads but every sub-path (api/status, api/pair, ...) 404s — verified on QuTS hero
#   6.0.1. We therefore manage our own marked block with slash-terminated rules.
#   Every reload is guarded by a config test with auto-revert, so a bad edit can
#   never leave the management panel un-reloadable.
APP_PROXY_CONF="/etc/app_proxy.conf"
PANEL_PROXY="/usr/local/apache/bin/apache_proxy"
PANEL_PROXYS="/usr/local/apache/bin/apache_proxys"
PANEL_CONF="/etc/apache-sys-proxy.conf"
PANEL_CONF_SSL="/etc/apache-sys-proxy-ssl.conf"
PROXY_BEGIN="# >>> TuneThatHue (managed by tunethathue.sh) >>>"
PROXY_END="# <<< TuneThatHue <<<"

web_port() {
    _p=$(/sbin/getcfg "$QPKG_NAME" Web_Port -f "$CONF_QPKG" 2>/dev/null)
    [ -n "$_p" ] || _p=58091
    echo "$_p"
}

proxy_reload_panel() {
    "$PANEL_PROXY"  -k graceful -f "$PANEL_CONF"     >/dev/null 2>&1
    "$PANEL_PROXYS" -k graceful -f "$PANEL_CONF_SSL" >/dev/null 2>&1
}

# Remove our marked block (in place; only ever deletes our own lines).
proxy_strip() {
    [ -f "$APP_PROXY_CONF" ] && \
        sed -i "/^# >>> TuneThatHue (managed by tunethathue.sh) >>>/,/^# <<< TuneThatHue <<</d" \
            "$APP_PROXY_CONF" 2>/dev/null
}

# Drop the bare qpkgd-generated rule. Apache matches ProxyPass in file order and the
# first match wins, so leaving it above our block would silently shadow the correct
# one — the panel 404s while the app on its own port keeps working. Runs after
# proxy_strip(), so anything still matching here is by definition not ours.
proxy_strip_foreign() {
    [ -f "$APP_PROXY_CONF" ] && \
        sed -i -E '/^[[:space:]]*ProxyPass[[:space:]]+"?\/TuneThatHue\/?"?([[:space:]]|$)/d' \
            "$APP_PROXY_CONF" 2>/dev/null
}

proxy_install() {
    _port=$(web_port)
    [ -f "$APP_PROXY_CONF" ] || touch "$APP_PROXY_CONF" 2>/dev/null
    cp -f "$APP_PROXY_CONF" "$APP_PROXY_CONF.tthbak" 2>/dev/null
    proxy_strip
    proxy_strip_foreign
    {
        echo "$PROXY_BEGIN"
        # bare /TuneThatHue -> /TuneThatHue/ : mod_rewrite runs before mod_proxy's
        # catch-all, so RedirectMatch/mod_alias would lose the race.
        echo 'RewriteEngine On'
        echo 'RewriteRule ^/TuneThatHue$ /TuneThatHue/ [R=302,L]'
        echo '<Location /TuneThatHue/>'
        echo '    RequestHeader set X-Forwarded-Host expr=%{HTTP:Host}'
        echo '    RequestHeader set X-Forwarded-Proto expr=%{REQUEST_SCHEME}'
        echo '    RequestHeader set X-Forwarded-Prefix /TuneThatHue'
        echo '</Location>'
        echo "ProxyPass        /TuneThatHue/ http://127.0.0.1:${_port}/"
        echo "ProxyPassReverse /TuneThatHue/ http://127.0.0.1:${_port}/"
        echo "$PROXY_END"
    } >> "$APP_PROXY_CONF"
    # Validate the WHOLE panel config with our block in place; revert if it breaks.
    if "$PANEL_PROXY" -t -f "$PANEL_CONF" >/dev/null 2>&1; then
        rm -f "$APP_PROXY_CONF.tthbak" 2>/dev/null
        proxy_reload_panel
        echo "TuneThatHue: panel route wired: /TuneThatHue/ -> 127.0.0.1:${_port}"
    else
        mv -f "$APP_PROXY_CONF.tthbak" "$APP_PROXY_CONF" 2>/dev/null
        echo "TuneThatHue: panel proxy config test FAILED - reverted; route NOT wired" >&2
    fi
}

proxy_remove() {
    proxy_strip
    "$PANEL_PROXY" -t -f "$PANEL_CONF" >/dev/null 2>&1 && proxy_reload_panel
}

case "$1" in
  start)
    ENABLED=$(/sbin/getcfg "$QPKG_NAME" Enable -u -d FALSE -f "$CONF_QPKG" 2>/dev/null)
    [ "$ENABLED" = "TRUE" ] || { echo "$QPKG_NAME is disabled."; exit 1; }
    # The payload is mirrored from a repo that may carry no exec bit.
    chmod 0755 "$QPKG_ROOT/tunethathue.sh" "$CTL" 2>/dev/null
    chmod 0755 "$QPKG_ROOT"/runtime/python-*/python/bin/python3* 2>/dev/null
    "$CTL" start
    proxy_install
    watchdog_cron_add
    ;;
  stop)
    watchdog_cron_remove
    proxy_remove
    "$CTL" stop
    ;;
  restart)
    watchdog_cron_remove
    "$CTL" restart
    proxy_install
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
