"""
The tray icon: start, stop, open the panel.

One file for Windows, macOS and Linux. It does no work of its own - every menu item
calls the same control command a terminal would, so the tray and the command line can
never disagree about what "start" means.

The icon changes with the state: lit when the daemon is answering, dim when it is not.
It is drawn in code rather than shipped as a file, because a tray icon has to exist at
16, 22, 32 and 44 pixels depending on the desktop, and four bars scale cleanly to all of
them.

Needs `pystray` and `Pillow`. Both are in the desktop bundle; without them the daemon
still runs and `tunethathue` still works from a terminal.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tunethathue_ctl as ctl  # noqa: E402

POLL_SECONDS = 5

# The panel's own colours, so the tray belongs to the same product.
LIVE = (51, 225, 160)
IDLE = (90, 98, 110)
BARS = ((0.16, 0.30), (0.38, 0.54), (0.60, 0.78), (0.82, 0.42))


def _icon(active: bool):
    """Four bars, the same mark the panel and the QNAP tile use."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colour = LIVE if active else IDLE
    width = 0.135 * size
    for centre, height in BARS:
        x = centre * size - width / 2
        draw.rounded_rectangle(
            [x, (0.86 - height) * size, x + width, 0.86 * size],
            radius=width * 0.32, fill=colour + (255,),
        )
    return image


def _daemon_answers() -> bool:
    import contextlib
    import urllib.request

    with contextlib.suppress(Exception):
        url = f"http://127.0.0.1:{ctl.panel_port()}/api/status"
        with urllib.request.urlopen(url, timeout=1):
            return True
    return False


def main() -> int:
    try:
        import pystray
    except ImportError:
        print("the tray needs pystray and Pillow; the daemon runs without it")
        print("  pip install pystray pillow")
        return 1

    state = {"running": _daemon_answers()}

    def run(command: str) -> None:
        # In its own thread: stopping a service can take a second and the menu should
        # not freeze while it happens.
        def worker() -> None:
            subprocess.run([sys.executable, str(ctl.__file__), command], check=False)
            time.sleep(1)
            refresh()

        threading.Thread(target=worker, daemon=True).start()

    def refresh() -> None:
        state["running"] = _daemon_answers()
        icon.icon = _icon(state["running"])
        icon.title = f"TuneThatHue - {'running' if state['running'] else 'stopped'}"
        icon.update_menu()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _: "Running" if state["running"] else "Stopped",
                         None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open panel", lambda: ctl.panel()),
        pystray.MenuItem("Start", lambda: run("start"),
                         visible=lambda _: not state["running"]),
        pystray.MenuItem("Stop", lambda: run("stop"),
                         visible=lambda _: state["running"]),
        pystray.MenuItem("Restart", lambda: run("restart")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit tray", lambda: icon.stop()),
    )
    icon = pystray.Icon("tunethathue", _icon(state["running"]), "TuneThatHue", menu)

    def watch() -> None:
        while True:
            time.sleep(POLL_SECONDS)
            was = state["running"]
            state["running"] = _daemon_answers()
            if was != state["running"]:
                icon.icon = _icon(state["running"])
                icon.title = f"TuneThatHue - {'running' if state['running'] else 'stopped'}"
                icon.update_menu()

    threading.Thread(target=watch, daemon=True).start()
    icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
