#!/usr/bin/env bash
# Install TuneThatHue on Ubuntu 24.04 or Debian.
#
# Everything goes under the user's own account: no root, no system-wide files. The
# daemon runs as a systemd *user* service, which means it starts at login and keeps
# running after logout (that is what `loginctl enable-linger` is for). Nothing here
# needs sudo except the two apt packages, and those are only for the tray icon.
#
#   ./install.sh            install and start
#   ./install.sh --no-tray  install without the tray icon
#   ./install.sh --remove   take it all off again

set -euo pipefail

APP_NAME="TuneThatHue"
PREFIX="${PREFIX:-$HOME/.local/share/tunethathue}"
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
  rm -f "$HOME/.config/autostart/tunethathue-tray.desktop"
  rm -f "$HOME/.local/share/applications/tunethathue.desktop"
  rm -rf "$PREFIX"
  echo "removed. your settings are still in ~/.local/state/$APP_NAME"
  exit 0
fi

# The effects engine is carried byte-for-byte from Music Assistant and uses PEP 758
# syntax, which is Python 3.14 and later. Ubuntu 24.04 ships 3.12, so on most machines we
# fetch a portable interpreter rather than send anyone to a PPA - the same approach the
# QNAP package already takes.
echo "==> python"
PYBIN=""
for candidate in python3.14 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,14) else 1)'; then
    PYBIN="$(command -v "$candidate")"
    echo "    using $PYBIN"
    break
  fi
done
if [ -z "$PYBIN" ]; then
  echo "    no Python 3.14 here; fetching a portable one"
  case "$(uname -m)" in
    x86_64)  PBS_ARCH="x86_64-unknown-linux-gnu" ;;
    aarch64) PBS_ARCH="aarch64-unknown-linux-gnu" ;;
    armv7l)  PBS_ARCH="armv7-unknown-linux-gnueabihf" ;;
    *) echo "no portable build for $(uname -m); install Python 3.14 yourself" >&2; exit 1 ;;
  esac
  mkdir -p "$PREFIX/runtime"
  PBS_URL="$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest |
    # the + in the version is percent-encoded in the API response
    grep -o "https://[^\"]*cpython-3[.]14[.][0-9.]*%2B[0-9]*-${PBS_ARCH}-install_only[.]tar[.]gz" |
    head -1)"
  if [ -z "$PBS_URL" ]; then
    echo "could not find a portable Python 3.14 for $PBS_ARCH" >&2
    exit 1
  fi
  echo "    $PBS_URL"
  curl -fsSL "$PBS_URL" | tar xz -C "$PREFIX/runtime"
  PYBIN="$PREFIX/runtime/python/bin/python3"
  "$PYBIN" -V
fi

echo "==> copying files to $PREFIX"
mkdir -p "$PREFIX"
for dir in python effects resources config install; do
  [ -d "$SRC/$dir" ] && cp -a "$SRC/$dir" "$PREFIX/"
done

# The decoder. Without it only uncompressed audio plays, so try to ship one: our own
# build if this tree has it, otherwise whatever the system already has.
ARCH="$(uname -m)"
mkdir -p "$PREFIX/runtime"
if [ -x "$SRC/runtime/ffmpeg-$ARCH/ffmpeg" ]; then
  cp -a "$SRC/runtime/ffmpeg-$ARCH" "$PREFIX/runtime/"
  echo "    bundled ffmpeg for $ARCH"
elif command -v ffmpeg >/dev/null; then
  echo "    using the system ffmpeg at $(command -v ffmpeg)"
else
  echo "    no ffmpeg found: only uncompressed audio will play"
  echo "    install one with:  sudo apt install ffmpeg"
fi

echo "==> python packages"
PIP_PKGS="aiohttp zeroconf aiosendspin hue-entertainment numpy"
[ "$WANT_TRAY" = "1" ] && PIP_PKGS="$PIP_PKGS pystray pillow"
"$PYBIN" -m venv "$PREFIX/venv"
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

echo "==> the service"
"$PREFIX/venv/bin/python" "$PREFIX/install/tunethathue_ctl.py" install

if [ "$WANT_TRAY" = "1" ]; then
  echo "==> the tray icon"
  # GNOME hides tray icons unless the AppIndicator extension is on; say so rather than
  # leaving someone hunting for an icon that is never going to appear.
  if [ "${XDG_CURRENT_DESKTOP:-}" = "GNOME" ] || [ "${XDG_CURRENT_DESKTOP:-}" = "ubuntu:GNOME" ]; then
    gnome-extensions list 2>/dev/null | grep -q appindicator \
      || echo "    note: GNOME needs the AppIndicator extension for tray icons"
  fi
  mkdir -p "$HOME/.config/autostart" "$HOME/.local/share/applications"
  cat > "$HOME/.config/autostart/tunethathue-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME tray
Exec=$PREFIX/venv/bin/python $PREFIX/install/tunethathue_tray.py
Icon=$PREFIX/resources/icon.png
X-GNOME-Autostart-enabled=true
EOF
  cat > "$HOME/.local/share/applications/tunethathue.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Sync Philips Hue lights to whatever is playing
Exec=$BIN_DIR/tunethathue panel
Icon=$PREFIX/resources/icon.png
Categories=AudioVideo;Audio;
EOF
  nohup "$PREFIX/venv/bin/python" "$PREFIX/install/tunethathue_tray.py" \
        >/dev/null 2>&1 & disown || true
fi

echo
echo "done."
echo "  panel:   http://127.0.0.1:8080"
echo "  control: tunethathue start | stop | status | panel"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  note: $BIN_DIR is not on your PATH yet - open a new terminal" ;;
esac
