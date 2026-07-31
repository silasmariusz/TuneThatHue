#!/bin/sh
# Copy the example config on first run, then hand over to the daemon.
#
# The config lives on the volume rather than in the image, so pulling a new image never
# costs anyone their paired bridge.
set -e
CONFIG="${TTH_CONFIG:-/config/hue-box.toml}"
mkdir -p "$(dirname "$CONFIG")"
if [ ! -f "$CONFIG" ]; then
  cp /app/config/hue-box.example.toml "$CONFIG"
  echo "wrote a fresh config at $CONFIG"
fi
exec python -u /app/python/tth_phase2.py --config "$CONFIG" "$@"
