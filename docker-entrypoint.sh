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
# ── the network mode, said out loud ─────────────────────────────────────────
#
# Three of the four inputs are found by multicast or broadcast: mDNS for Sendspin, SSDP
# for the DLNA renderer, a UDP broadcast for Squeezebox. None of that crosses Docker's
# default bridge network. On bridge the container starts, the panel opens, and then
# nothing ever finds it - which looks like a broken daemon rather than a network mode,
# and is the thing most likely to waste somebody's evening.
#
# Docker hands bridge endpoints MAC addresses out of its own 02:42 range, so that is
# what is checked. Host and macvlan networks show a real adapter instead.
iface=$(awk '$2 == "00000000" { print $1; exit }' /proc/net/route 2>/dev/null || true)
mac=""
if [ -n "$iface" ] && [ -r "/sys/class/net/$iface/address" ]; then
  mac=$(cat "/sys/class/net/$iface/address")
fi

case "$mac" in
  02:42:*)
    echo ""
    echo "  #########################################################"
    echo "  ##  This container is on a Docker BRIDGE network.      ##"
    echo "  ##                                                     ##"
    echo "  ##  Sendspin, DLNA and Squeezebox are all found by     ##"
    echo "  ##  multicast or broadcast, and none of that crosses   ##"
    echo "  ##  a bridge. Nothing on your network will see this    ##"
    echo "  ##  container, and publishing ports will not help.     ##"
    echo "  ##                                                     ##"
    echo "  ##  Use:  --network host                               ##"
    echo "  ##  or a macvlan network to give it its own address.   ##"
    echo "  ##                                                     ##"
    echo "  ##  The panel and Snapcast still work as they are.     ##"
    echo "  #########################################################"
    echo ""
    ;;
  "")
    echo "[net] could not tell what network this is on; carrying on"
    ;;
  *)
    echo "[net] on $iface ($mac) - not a Docker bridge, discovery can work"
    ;;
esac

exec python -u /app/python/tth_phase2.py --config "$CONFIG" "$@"
