#!/bin/sh
##
##   ███████ ██   ██ ▄█████▄ ▄██████ ███████ ██   ██ ▄█████▄ ███████ ██   ██ ██   ██ ▄██████
##     ▐█▌   ██   ██ ██   ██ ██▄▄▄▄    ▐█▌   ██▄▄▄██ ██▄▄▄██   ▐█▌   ██▄▄▄██ ██   ██ ██▄▄▄▄
##     ▐█▌   ██   ██ ██   ██ ██▀▀▀▀    ▐█▌   ██▀▀▀██ ██▀▀▀██   ▐█▌   ██▀▀▀██ ██   ██ ██▀▀▀▀
##     ▐█▌   ▀█████▀ ██   ██ ▀██████   ▐█▌   ██   ██ ██   ██   ▐█▌   ██   ██ ▀█████▀ ▀██████
##
##   Copyright © 2008-2027, Silas Mariusz <silas@qnap.com.antispam>
##                          QNAP Club Polska
##
##   www.qnaputilities.com  forum.qnap.net.pl
##
##   LICENSE: Apache-2.0
##
##   ┌─────────┬───────────────────────────────┐
##   │ File    │ qnap/shared/tunethathue.sh    │
##   ├─────────┼───────────────────────────────┤
##   │ Package │ TuneThatHue, QPKG TuneThatHue │
##   ├─────────┼───────────────────────────────┤
##   │ Runs on │ the NAS                       │
##   └─────────┴───────────────────────────────┘
##
# TuneThatHue: QTS service script
#
#   tunethathue.sh start|stop|restart|status
#   tunethathue.sh watchdog   what the cron line runs every two minutes
#
# QTS runs this file as root through /etc/init.d/tunethathue.sh: at every boot for every installed package, a
# switched-off one too; from App Center's switch (qpkg_cli --enable and --disable run start and stop); from
# qpkg_cli --start and --stop; from the installer (the old script's stop before an upgrade, start after it); and
# from the uninstaller (stop, then remove). What it does:
#   - start does nothing and exits 1 unless Enable in /etc/config/qpkg.conf is TRUE. App Center's switch writes
#     that key; restart and the watchdog obey it too.
#   - start runs the daemon (the bundled Python with app/python/tth_phase2.py) and makes three things outside the
#     package folder, which stop takes away again: the run marker /var/run/tunethathue.run, the panel route block in
#     /etc/app_proxy.conf and the watchdog line in /etc/config/crontab. PKG_POST_REMOVE (package_routines) removes
#     them once more at uninstall.
#   - stop ends only this installation's daemon: a process whose /proc/<pid>/exe is the bundled Python (same file,
#     so the two names QuTS hero gives one folder do not matter) and whose command line runs tth_phase2.py.
#   - the watchdog restarts a daemon that died, and only while the package is installed (Status complete), Enable
#     is TRUE and the run marker is there. When Enable is not TRUE it removes its own cron line.
#   - QTS starts package scripts with umask 0000. The script works with umask 077 and closes config/ (it holds the
#     paired bridge's keys) and var/ at every start.
#
# POSIX sh (QTS runs it with bash 3.2; nothing here needs more than BusyBox ash). Outside the shell it uses
# /sbin/getcfg, /usr/bin/crontab and the panel's own apache_proxy, and the package's own Python.

PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
umask 077
# QTS's installer runs start inside a temporary folder that it deletes afterwards
cd / || exit 1

QPKG_NAME="TuneThatHue"
QPKG_CONF="/etc/config/qpkg.conf"
QPKG_ROOT=$(/sbin/getcfg "$QPKG_NAME" Install_Path -f "$QPKG_CONF" 2>/dev/null)
if [ -z "$QPKG_ROOT" ] || [ ! -d "$QPKG_ROOT" ]; then
	_self=$(readlink -f "$0" 2>/dev/null)
	QPKG_ROOT=${_self%/*}
fi
SELF="$QPKG_ROOT/tunethathue.sh"
APP="$QPKG_ROOT/app"
CONF_DIR="$QPKG_ROOT/config"
CONF="$CONF_DIR/hue-box.toml"
VAR="$QPKG_ROOT/var"
RUN="$VAR/run"
LOGDIR="$VAR/log"
PIDFILE="$RUN/tunethathue.pid"
LOG="$LOGDIR/tunethathue.log"
LOCK="$RUN/service.lock"
MARKER="/var/run/tunethathue.run"
VBAN_PORT=6980
# the daemon writes a stats line a second (about 9 MB a day); at 8 MiB the last 1 MiB moves to tunethathue.log.1
LOG_MAX=8388608
LOG_KEEP=1048576

CRONTAB="/etc/config/crontab"
WD_MARK="# TuneThatHue-watchdog"

APP_PROXY_CONF="/etc/app_proxy.conf"
PANEL_PROXY="/usr/local/apache/bin/apache_proxy"
PANEL_PROXYS="/usr/local/apache/bin/apache_proxys"
PANEL_CONF="/etc/apache-sys-proxy.conf"
PANEL_CONF_SSL="/etc/apache-sys-proxy-ssl.conf"
PROXY_BEGIN="# >>> TuneThatHue (managed by tunethathue.sh) >>>"
PROXY_END="# <<< TuneThatHue <<<"

say() {
	echo "TuneThatHue: $*"
}

enabled() {
	[ "$(/sbin/getcfg "$QPKG_NAME" Enable -u -d FALSE -f "$QPKG_CONF" 2>/dev/null)" = "TRUE" ]
}

web_port() {
	_p=$(/sbin/getcfg "$QPKG_NAME" Web_Port -d 58091 -f "$QPKG_CONF" 2>/dev/null)
	case "$_p" in '' | *[!0-9]*) _p=58091 ;; esac
	echo "$_p"
}

arch_detect() {
	case "$(uname -m)" in
	x86_64) echo x86_64 ;;
	aarch64 | arm64) echo aarch64 ;;
	armv7l | armv7 | armhf) echo armv7 ;;
	*) uname -m ;;
	esac
}

python_bin() {
	echo "$QPKG_ROOT/runtime/python-$(arch_detect)/python/bin/python3"
}

# ── one run at a time ────────────────────────────────────────────────────────────────────────────────────────────
# A lock folder: start, stop and restart wait for it (30 s); the watchdog skips its turn when it is taken, so a cron
# run cannot start the daemon again while stop is ending it.
lock_free() {
	rm -f "${LOCK:?}/pid"
	rmdir "$LOCK" 2>/dev/null
}

lock_try() {
	mkdir -p "$RUN" 2>/dev/null
	if mkdir "$LOCK" 2>/dev/null; then
		echo $$ >"$LOCK/pid"
		trap lock_free EXIT
		return 0
	fi
	_owner=$(cat "$LOCK/pid" 2>/dev/null)
	if [ -n "$_owner" ] && kill -0 "$_owner" 2>/dev/null; then
		return 1
	fi
	# left by a run that was killed
	lock_free
	mkdir "$LOCK" 2>/dev/null || return 1
	echo $$ >"$LOCK/pid"
	trap lock_free EXIT
	return 0
}

lock_take() {
	_i=0
	until lock_try; do
		_i=$((_i + 1))
		if [ "$_i" -ge 30 ]; then
			say "another run of tunethathue.sh holds $LOCK; nothing was done"
			exit 1
		fi
		sleep 1
	done
}

# ── files ────────────────────────────────────────────────────────────────────────────────────────────────────────
# config/ holds the paired bridge's application key and client key; the log names the bridge and the network
lock_down() {
	mkdir -p "$CONF_DIR" "$RUN" "$LOGDIR"
	chmod 0700 "$CONF_DIR" "$VAR" "$RUN" "$LOGDIR" 2>/dev/null
	for _f in "$CONF_DIR"/* "$LOGDIR"/*; do
		[ -f "$_f" ] && chmod 0600 "$_f"
	done
	return 0
}

log_cap() {
	[ -f "$LOG" ] || return 0
	_size=$(wc -c <"$LOG" 2>/dev/null)
	_size=$((_size + 0))
	[ "$_size" -gt "$LOG_MAX" ] || return 0
	tail -c "$LOG_KEEP" "$LOG" >"$LOG.1" 2>/dev/null
	# in place: the daemon appends (O_APPEND), so it goes on writing at the new end
	: >"$LOG"
	echo "$(date) the log reached $_size bytes and was cut; its last $LOG_KEEP bytes are in tunethathue.log.1" >>"$LOG"
}

# App Center's tile. QTS copies the icons at installation and at boot. They are its interface cache, not a link
# made for a person, so they are refreshed for a switched-off package too and stay after stop. The file is deleted
# first: a placeholder there can be a symlink to the shared no_qpkg_icon_64.gif, and a copy onto it would replace
# the placeholder of every other package.
install_icons() {
	[ -d /home/httpd/RSS/images ] || return 0
	for _suffix in "" "_gray" "_80"; do
		_src="$QPKG_ROOT/.qpkg_icon${_suffix}.gif"
		_dst="/home/httpd/RSS/images/${QPKG_NAME}${_suffix}.gif"
		[ -f "$_src" ] || continue
		rm -f "$_dst"
		cp "$_src" "$_dst" && chmod 0644 "$_dst"
	done
	return 0
}

# ── the daemon ───────────────────────────────────────────────────────────────────────────────────────────────────
our_pids() {
	_py=$(python_bin)
	for _d in /proc/[0-9]*; do
		# shellcheck disable=SC3013 # -ef: bash 3.2 (QTS's /bin/sh) and BusyBox ash both have it
		[ "$_d/exe" -ef "$_py" ] || continue
		case "$(tr '\000' ' ' <"$_d/cmdline" 2>/dev/null)" in
		*"/app/python/tth_phase2.py "*) echo "${_d#/proc/}" ;;
		esac
	done
}

start_daemon() {
	_pid=$(our_pids | head -n 1)
	if [ -n "$_pid" ]; then
		echo "$_pid" >"$PIDFILE"
		say "already running (pid $_pid)"
		return 0
	fi
	_py=$(python_bin)
	if [ ! -x "$_py" ]; then
		say "the bundled Python for $(arch_detect) is missing: $_py"
		return 1
	fi
	# the first start gets the example settings, so the web page has something to show before pairing
	[ -f "$CONF" ] || cp "$APP/config/hue-box.example.toml" "$CONF" 2>/dev/null
	lock_down
	log_cap
	_port=$(web_port)
	(
		trap - EXIT
		trap '' HUP
		exec </dev/null >>"$LOG" 2>&1
		# -u: unbuffered, so the log is current when someone reads it
		"$_py" -u "$APP/python/tth_phase2.py" --output none --config "$CONF" \
			--port "$VBAN_PORT" --webui-port "$_port" &
		echo $! >"$PIDFILE"
	) &
	_i=0
	while [ "$_i" -lt 15 ]; do
		_pid=$(our_pids | head -n 1)
		[ -n "$_pid" ] && break
		sleep 1
		_i=$((_i + 1))
	done
	if [ -z "$_pid" ]; then
		say "the daemon did not start; the reason is at the end of $LOG"
		return 1
	fi
	sleep 2
	if [ -z "$(our_pids)" ]; then
		say "the daemon stopped right after it started; the reason is at the end of $LOG"
		return 1
	fi
	say "started (pid $_pid), web page on port $_port"
	return 0
}

stop_daemon() {
	_pids=$(our_pids)
	if [ -n "$_pids" ]; then
		# shellcheck disable=SC2086 # one word per pid
		kill -TERM $_pids 2>/dev/null
		_i=0
		while [ "$_i" -lt 10 ] && [ -n "$(our_pids)" ]; do
			sleep 1
			_i=$((_i + 1))
		done
		_pids=$(our_pids)
		# shellcheck disable=SC2086
		[ -n "$_pids" ] && kill -KILL $_pids 2>/dev/null
	fi
	rm -f "${RUN:?}/tunethathue.pid"
	return 0
}

# ── the panel route: /TuneThatHue/ on the NAS's own web port -> 127.0.0.1:<Web_Port> ─────────────────────────────
# QTS's panel Apache includes /etc/app_proxy.conf. For a package with Use_Proxy = 1, QTS itself writes
#     ProxyPass "<Proxy_Path>" "http://127.0.0.1:<Web_Port>/" retry=0
# there, and after every switch-on in App Center it puts that line above this block. Up to 0.9.9 Proxy_Path was
# /TuneThatHue: that line sent /TuneThatHue/x to the daemon as //x, which it answers with 404, so App Center's Open
# showed "Not Found". Since 0.9.10 Proxy_Path is /TuneThatHue/ and QTS's line maps the same way as this block, in
# whichever order the two come. The block adds what QTS's line has not: the redirect of the bare /TuneThatHue, the
# forwarded headers and ProxyPassReverse, and the route on a firmware that writes no line. Every reload is preceded
# by a config test with an automatic revert, so an edit cannot leave the management panel unable to reload.
proxy_reload_panel() {
	"$PANEL_PROXY" -k graceful -f "$PANEL_CONF" >/dev/null 2>&1
	"$PANEL_PROXYS" -k graceful -f "$PANEL_CONF_SSL" >/dev/null 2>&1
}

proxy_strip() {
	[ -f "$APP_PROXY_CONF" ] || return 0
	sed -i "/^# >>> TuneThatHue (managed by tunethathue.sh) >>>/,/^# <<< TuneThatHue <<</d" "$APP_PROXY_CONF"
	# QTS's line from the old Proxy_Path (no slash), left by an upgrade from 0.9.9 or older
	sed -i -E '/^[[:space:]]*ProxyPass[[:space:]]+"?\/TuneThatHue"?[[:space:]]/d' "$APP_PROXY_CONF"
}

proxy_install() {
	_port=$(web_port)
	[ -x "$PANEL_PROXY" ] || return 0
	[ -f "$APP_PROXY_CONF" ] || { : >"$APP_PROXY_CONF" && chmod 0644 "$APP_PROXY_CONF"; }
	cp -p "$APP_PROXY_CONF" "$APP_PROXY_CONF.tthbak"
	proxy_strip
	{
		echo "$PROXY_BEGIN"
		# mod_rewrite runs before the panel's catch-all ProxyPass, so the bare path redirects first
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
	} >>"$APP_PROXY_CONF"
	if "$PANEL_PROXY" -t -f "$PANEL_CONF" >/dev/null 2>&1; then
		rm -f "$APP_PROXY_CONF.tthbak"
		proxy_reload_panel
		say "panel route /TuneThatHue/ -> 127.0.0.1:${_port}"
	else
		mv -f "$APP_PROXY_CONF.tthbak" "$APP_PROXY_CONF"
		say "the panel's config test failed with the route in it; $APP_PROXY_CONF is back as it was and the route is not set"
	fi
}

proxy_remove() {
	[ -f "$APP_PROXY_CONF" ] || return 0
	grep -qF "$PROXY_BEGIN" "$APP_PROXY_CONF" || return 0
	proxy_strip
	"$PANEL_PROXY" -t -f "$PANEL_CONF" >/dev/null 2>&1 && proxy_reload_panel
	return 0
}

# ── the watchdog's cron line ─────────────────────────────────────────────────────────────────────────────────────
# /etc/config/crontab outlives a reboot, so start writes the line and stop takes it away
cron_line() {
	echo "*/2 * * * * /bin/sh $SELF watchdog >/dev/null 2>&1 $WD_MARK"
}

# Up to 0.9.9 the line was removed through a temporary file moved over the crontab; under QTS's umask 0000 that left
# root's crontab writable by every user (0666). Each edit here closes it again (the firmware's mode is 0644).
cron_close() {
	chmod go-w "$CRONTAB" 2>/dev/null
}

cron_strip() {
	[ -f "$CRONTAB" ] || return 0
	cron_close
	grep -qF "$WD_MARK" "$CRONTAB" || return 0
	sed -i "/$WD_MARK/d" "$CRONTAB"
	crontab "$CRONTAB" 2>/dev/null
	return 0
}

cron_add() {
	[ -f "$CRONTAB" ] || return 0
	cron_close
	_line=$(cron_line)
	grep -qxF "$_line" "$CRONTAB" && return 0
	# a line of an older version (it called etc/tunethathue/daemon.sh) goes first
	sed -i "/$WD_MARK/d" "$CRONTAB"
	echo "$_line" >>"$CRONTAB"
	crontab "$CRONTAB" 2>/dev/null
	return 0
}

# ── the verbs ────────────────────────────────────────────────────────────────────────────────────────────────────
do_start() {
	install_icons
	if ! enabled; then
		say "switched off in App Center (Enable is not TRUE in $QPKG_CONF), so it does not start. Switch it on in App Center to start it."
		exit 1
	fi
	lock_down
	chmod 0755 "$SELF" 2>/dev/null
	: >"$MARKER"
	start_daemon
	_rc=$?
	proxy_install
	# also after a failed start: Enable is TRUE, so the watchdog tries again every two minutes
	cron_add
	return "$_rc"
}

do_stop() {
	# the marker goes first: a watchdog run after this point starts nothing
	rm -f /var/run/tunethathue.run
	cron_strip
	proxy_remove
	stop_daemon
	say "stopped"
	return 0
}

do_status() {
	_pid=$(our_pids | head -n 1)
	if [ -n "$_pid" ]; then
		say "running (pid $_pid), web page on port $(web_port)"
		return 0
	fi
	if enabled; then
		say "stopped"
	else
		say "stopped; switched off in App Center"
	fi
	return 1
}

do_watchdog() {
	lock_try || return 0
	# an installation in progress replaces the files under the daemon
	[ "$(/sbin/getcfg "$QPKG_NAME" Status -f "$QPKG_CONF" 2>/dev/null)" = "complete" ] || return 0
	if ! enabled; then
		rm -f /var/run/tunethathue.run
		cron_strip
		return 0
	fi
	# stop removed it, or start has not run since the NAS booted
	[ -f "$MARKER" ] || return 0
	log_cap
	if [ -f "$APP_PROXY_CONF" ] && ! grep -qF "$PROXY_BEGIN" "$APP_PROXY_CONF"; then
		proxy_install >/dev/null
	fi
	[ -n "$(our_pids)" ] && return 0
	echo "$(date) the daemon is not running; the watchdog starts it" >>"$LOG"
	start_daemon >>"$LOG" 2>&1
}

case "$1" in
start)
	lock_take
	do_start
	;;
stop)
	lock_take
	do_stop
	;;
restart)
	lock_take
	do_stop
	do_start
	;;
status)
	do_status
	;;
watchdog)
	do_watchdog
	;;
remove)
	# the uninstaller calls it after stop; PKG_POST_REMOVE in package_routines does the rest
	:
	;;
*)
	echo "usage: $0 {start|stop|restart|status}" >&2
	exit 2
	;;
esac
