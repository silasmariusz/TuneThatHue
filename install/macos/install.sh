#!/usr/bin/env bash
# Install TuneThatHue on macOS.
#
# Same shape as the Linux one: everything under the user's own account, no root. The
# daemon runs as a launchd agent, so it starts at login and launchd restarts it if it
# dies. The menu-bar icon is the same tray app the other platforms use.
#
#   ./install.sh            install and start
#   ./install.sh --no-tray  install without the menu-bar icon
#   ./install.sh --remove   take it all off again

set -euo pipefail

APP_NAME="TuneThatHue"
PREFIX="${PREFIX:-$HOME/Library/Application Support/TuneThatHue/app}"
BIN_DIR="$HOME/.local/bin"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WANT_TRAY=1

for arg in "$@"; do
  case "$arg" in
    --no-tray) WANT_TRAY=0 ;;
    --remove)  REMOVE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "${REMOVE:-0}" = "1" ]; then
  python3 "$PREFIX/install/tunethathue_ctl.py" uninstall || true
  rm -f "$BIN_DIR/tunethathue"
  rm -f "$HOME/Library/LaunchAgents/pl.devspark.tunethathue-tray.plist"
  rm -rf "$PREFIX"
  echo "removed. your settings are still in ~/Library/Application Support/$APP_NAME"
  exit 0
fi

# 3.14 and later only - the engine uses PEP 758 syntax. When the Mac has no 3.14,
# fetch a portable one rather than send anyone to Homebrew for it.
echo "==> python"
PY_BIN=""
for candidate in python3.14 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,14) else 1)'; then
    PY_BIN="$(command -v "$candidate")"
    echo "    using $PY_BIN"
    break
  fi
done
if [ -z "$PY_BIN" ]; then
  echo "    no Python 3.14 here; fetching a portable one"
  case "$(uname -m)" in
    arm64)  PBS_ARCH="aarch64-apple-darwin" ;;
    x86_64) PBS_ARCH="x86_64-apple-darwin" ;;
    *) echo "no portable build for $(uname -m)" >&2; exit 1 ;;
  esac
  mkdir -p "$PREFIX/runtime"
  PBS_URL="$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest |
    # the + in the version is percent-encoded in the API response
    grep -o "https://[^\"]*cpython-3[.]14[.][0-9.]*%2B[0-9]*-${PBS_ARCH}-install_only[.]tar[.]gz" |
    head -1)"
  [ -n "$PBS_URL" ] || { echo "could not find a portable Python 3.14" >&2; exit 1; }
  curl -fsSL "$PBS_URL" | tar xz -C "$PREFIX/runtime"
  PY_BIN="$PREFIX/runtime/python/bin/python3"
  xattr -dr com.apple.quarantine "$PREFIX/runtime/python" 2>/dev/null || true
  "$PY_BIN" -V
fi

echo "==> copying files"
mkdir -p "$PREFIX"
for dir in python effects resources config install; do
  [ -d "$SRC/$dir" ] && cp -a "$SRC/$dir" "$PREFIX/"
done

ARCH="$(uname -m)"
mkdir -p "$PREFIX/runtime"
if [ -x "$SRC/runtime/ffmpeg-$ARCH/ffmpeg" ]; then
  cp -a "$SRC/runtime/ffmpeg-$ARCH" "$PREFIX/runtime/"
  # Gatekeeper quarantines anything that arrived from a browser; without this the first
  # decode dies with "cannot be opened because the developer cannot be verified".
  xattr -dr com.apple.quarantine "$PREFIX/runtime/ffmpeg-$ARCH" 2>/dev/null || true
  echo "    bundled ffmpeg for $ARCH"
elif command -v ffmpeg >/dev/null; then
  echo "    using the system ffmpeg at $(command -v ffmpeg)"
else
  echo "    no ffmpeg found: only uncompressed audio will play"
  echo "    install one with:  brew install ffmpeg"
fi

echo "==> python packages"
PIP_PKGS="aiohttp zeroconf aiosendspin hue-entertainment numpy"
[ "$WANT_TRAY" = "1" ] && PIP_PKGS="$PIP_PKGS pystray pillow pyobjc-framework-Cocoa"
"$PY_BIN" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
# shellcheck disable=SC2086
"$PREFIX/venv/bin/pip" install --quiet $PIP_PKGS

echo "==> the tunethathue command"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/tunethathue" <<EOF
#!/bin/sh
exec "$PREFIX/venv/bin/python" "$PREFIX/install/tunethathue_ctl.py" "\$@"
EOF
chmod +x "$BIN_DIR/tunethathue"

echo "==> the launchd agent"
"$PREFIX/venv/bin/python" "$PREFIX/install/tunethathue_ctl.py" install

if [ "$WANT_TRAY" = "1" ]; then
  echo "==> the menu-bar icon"
  PLIST="$HOME/Library/LaunchAgents/pl.devspark.tunethathue-tray.plist"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>pl.devspark.tunethathue-tray</string>
  <key>ProgramArguments</key><array>
    <string>$PREFIX/venv/bin/python</string>
    <string>$PREFIX/install/tunethathue_tray.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict></plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load -w "$PLIST"
fi

echo
echo "done."
echo "  panel:   http://127.0.0.1:8080"
echo "  control: tunethathue start | stop | status | panel"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  note: add $BIN_DIR to your PATH, or call the command by its full path" ;;
esac
