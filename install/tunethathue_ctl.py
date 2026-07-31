"""
One command that starts, stops and checks the daemon on any of the three desktops.

The tray icons on Windows, macOS and Linux are front ends for this. So is the installer.
Keeping every platform behind one command means a bug gets fixed once, and anything the
tray can do can also be done from a terminal or a script:

    tunethathue start | stop | restart | status | panel | install | uninstall
    tunethathue codecs | getffmpeg

Each platform has its own idea of what a service is - a Windows service, a launchd job,
a systemd unit - so `install` writes the right one and `start` talks to it. When no
service is installed, the same commands fall back to running the daemon as a plain
background process, which is what you want while trying it out.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVICE_NAME = "tunethathue"
DISPLAY_NAME = "TuneThatHue"
DEFAULT_PANEL_PORT = 8080

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DAEMON = ROOT / "python" / "tth_phase2.py"


# -- where things live -------------------------------------------------------------


def python_exe() -> str:
    """The interpreter to run the daemon with: the bundled one if we ship one."""
    for candidate in (
        ROOT / "runtime" / "python" / "bin" / "python3",
        ROOT / "runtime" / "python" / "python.exe",
        ROOT / "venv" / "bin" / "python",
        ROOT / "venv" / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def state_dir() -> Path:
    """Somewhere writable for the pid file and the log, per platform convention."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = base / DISPLAY_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    """The config file, created from the example on first run."""
    path = state_dir() / "hue-box.toml"
    if not path.exists():
        example = ROOT / "config" / "hue-box.example.toml"
        if example.is_file():
            shutil.copyfile(example, path)
    return path


def panel_port() -> int:
    """Read the panel port out of the config, so `panel` opens the right page."""
    try:
        import tomllib

        with config_path().open("rb") as fh:
            return int(tomllib.load(fh).get("webui", {}).get("port", DEFAULT_PANEL_PORT))
    except Exception:  # noqa: BLE001 - the default is fine
        return DEFAULT_PANEL_PORT


def _run(cmd: list[str]) -> int:
    """Run a command, ignore its output, return its exit code."""
    return subprocess.run(cmd, capture_output=True, check=False).returncode


def _run_text(cmd: list[str]) -> str:
    """
    Run a command and read its output as text, whatever the console code page is.

    Windows tools answer in the system locale, and on a non-English Windows that is not
    UTF-8 - decoding it strictly raises and takes the caller down with it.
    """
    out = subprocess.run(cmd, capture_output=True, check=False).stdout
    return out.decode("utf-8", errors="replace")


# -- the plain background process, used when no service is installed ------------------


def _pid_file() -> Path:
    return state_dir() / "daemon.pid"


def _running_pid() -> int | None:
    try:
        pid = int(_pid_file().read_text().strip())
    except (OSError, ValueError):
        return None
    if platform.system() == "Windows":
        out = _run_text(["tasklist", "/FI", f"PID eq {pid}"])
        return pid if str(pid) in out else None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def _spawn() -> int:
    log = (state_dir() / "daemon.log").open("ab")
    creation = 0x08000000 if platform.system() == "Windows" else 0    # no console window
    proc = subprocess.Popen(
        [python_exe(), "-u", str(DAEMON), "--config", str(config_path())],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=creation, cwd=str(ROOT),
        start_new_session=(platform.system() != "Windows"),
    )
    _pid_file().write_text(str(proc.pid))
    return proc.pid


def _kill(pid: int) -> None:
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
        return
    import signal

    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            return
    os.kill(pid, signal.SIGKILL)


# -- services ------------------------------------------------------------------------


def _service_installed() -> bool:
    system = platform.system()
    if system == "Windows":
        return _run(["sc", "query", SERVICE_NAME]) == 0
    if system == "Darwin":
        return (Path.home() / "Library" / "LaunchAgents"
                / f"pl.devspark.{SERVICE_NAME}.plist").is_file()
    return _systemd_unit().is_file()


def _systemd_unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _service(action: str) -> bool:
    """Ask the platform's service manager to do something. False if it has no service."""
    if not _service_installed():
        return False
    system = platform.system()
    if system == "Windows":
        verb = {"start": "start", "stop": "stop"}.get(action)
        if verb:
            subprocess.run(["sc", verb, SERVICE_NAME], capture_output=True, check=False)
        return True
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"pl.devspark.{SERVICE_NAME}.plist"
        verb = {"start": "load", "stop": "unload"}.get(action)
        if verb:
            subprocess.run(["launchctl", verb, "-w", str(plist)],
                           capture_output=True, check=False)
        return True
    subprocess.run(["systemctl", "--user", action, SERVICE_NAME],
                   capture_output=True, check=False)
    return True


# -- commands ------------------------------------------------------------------------


def start() -> None:
    if _service("start"):
        print("started (service)")
        return
    if _running_pid():
        print("already running")
        return
    print(f"started (pid {_spawn()})")


def stop() -> None:
    if _service("stop"):
        print("stopped (service)")
        return
    pid = _running_pid()
    if pid is None:
        print("not running")
        return
    _kill(pid)
    _pid_file().unlink(missing_ok=True)
    print("stopped")


def restart() -> None:
    stop()
    time.sleep(1)
    start()


def status() -> int:
    """Print what is going on, and return 0 when the daemon is answering."""
    port = panel_port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
            data = json.loads(resp.read())
        sources = [name for name in ("sendspin", "snapcast", "slimproto", "dlna")
                   if data.get(name)]
        print(f"running   panel http://127.0.0.1:{port}")
        print(f"output    {data.get('output', '-')}   driving: {data.get('driving') or '-'}")
        print(f"inputs    {', '.join(sources) if sources else 'vban only'}")
        return 0
    except (urllib.error.URLError, TimeoutError, OSError):
        pid = _running_pid()
        print(f"starting (pid {pid})" if pid else "not running")
        return 1


def panel() -> None:
    import webbrowser

    webbrowser.open(f"http://127.0.0.1:{panel_port()}/")


def install_service() -> None:
    """Write the platform's service file so the daemon starts by itself."""
    system = platform.system()
    if system == "Linux":
        unit = _systemd_unit()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "[Unit]\n"
            f"Description={DISPLAY_NAME}\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            f"ExecStart={python_exe()} -u {DAEMON} --config {config_path()}\n"
            f"WorkingDirectory={ROOT}\n"
            "Restart=always\n"
            "RestartSec=3\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=False)
        # Without this the unit stops when the user logs out.
        subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")], check=False)
        print(f"installed: systemd user unit at {unit}")
        return

    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"pl.devspark.{SERVICE_NAME}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f"  <key>Label</key><string>pl.devspark.{SERVICE_NAME}</string>\n"
            "  <key>ProgramArguments</key><array>\n"
            f"    <string>{python_exe()}</string><string>-u</string>\n"
            f"    <string>{DAEMON}</string>\n"
            f"    <string>--config</string><string>{config_path()}</string>\n"
            "  </array>\n"
            "  <key>RunAtLoad</key><true/>\n"
            "  <key>KeepAlive</key><true/>\n"
            f"  <key>WorkingDirectory</key><string>{ROOT}</string>\n"
            f"  <key>StandardOutPath</key><string>{state_dir() / 'daemon.log'}</string>\n"
            f"  <key>StandardErrorPath</key><string>{state_dir() / 'daemon.log'}</string>\n"
            "</dict></plist>\n"
        )
        subprocess.run(["launchctl", "load", "-w", str(plist)], check=False)
        print(f"installed: launchd job at {plist}")
        return

    if system == "Windows":
        # sc.exe wants one flat command line, and the quoting has to survive it.
        binpath = f'"{python_exe()}" -u "{DAEMON}" --config "{config_path()}"'
        subprocess.run(
            ["sc", "create", SERVICE_NAME, "binPath=", binpath, "start=", "auto",
             "DisplayName=", DISPLAY_NAME],
            check=False,
        )
        subprocess.run(["sc", "description", SERVICE_NAME,
                        "Sync Philips Hue lights to whatever is playing"], check=False)
        subprocess.run(["sc", "start", SERVICE_NAME], check=False)
        print("installed: Windows service")
        return

    print(f"no service support for {system}")


def uninstall_service() -> None:
    system = platform.system()
    if system == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
        _systemd_unit().unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    elif system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"pl.devspark.{SERVICE_NAME}.plist"
        subprocess.run(["launchctl", "unload", "-w", str(plist)], check=False)
        plist.unlink(missing_ok=True)
    elif system == "Windows":
        subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True, check=False)
        subprocess.run(["sc", "delete", SERVICE_NAME], check=False)
    print("service removed")


# -- the decoder --------------------------------------------------------------------

# What has to work for the inputs to accept what senders actually send.
REQUIRED_CODECS = ("flac", "mp3", "aac", "vorbis", "opus", "alac", "wmav2", "pcm_s16le")


def ffmpeg_path() -> str | None:
    """Find a decoder: ours first, then the system's, then anything on PATH."""
    arch = platform.machine()
    for candidate in (
        ROOT / "runtime" / f"ffmpeg-{arch}" / "ffmpeg",
        ROOT / "runtime" / f"ffmpeg-{arch}" / "ffmpeg.exe",
        Path("/usr/bin/ffmpeg"),
        Path("/usr/local/medialibrary/bin/ffmpeg"),
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def check_codecs(path: str | None = None) -> int:
    """
    Say which of the codecs we need this ffmpeg can decode.

    Worth doing rather than assuming: the one shipped with a NAS is often built without
    AAC, and finding that out when a track will not play is worse than finding it out
    now. Returns 0 when everything we need is there.
    """
    path = path or ffmpeg_path()
    if not path:
        print("no ffmpeg found. Only uncompressed audio will play.")
        print("run:  tunethathue getffmpeg")
        return 1
    print(f"ffmpeg: {path}")
    out = subprocess.run([path, "-hide_banner", "-decoders"],
                         capture_output=True, check=False).stdout.decode("utf-8", "replace")
    have = {line.split()[1] for line in out.splitlines()
            if line.startswith(" A") and len(line.split()) > 1}
    missing = [c for c in REQUIRED_CODECS if c not in have]
    for codec in REQUIRED_CODECS:
        print(f"  {'ok ' if codec in have else 'MISSING'}  {codec}")
    if missing:
        print(f"\n{len(missing)} missing. Music in those formats will not play.")
        print("run:  tunethathue getffmpeg")
        return 1
    print("\nall present.")
    return 0


def get_ffmpeg() -> None:
    """
    Point the box at a decoder: download one, or use one that is already here.

    Downloading is offered rather than assumed. Plenty of people already have an ffmpeg
    they trust, and on a metered connection nobody wants a surprise download.
    """
    current = ffmpeg_path()
    print(f"current: {current or 'none'}")
    print("\n  1  download a build for this machine")
    print("  2  use one I already have (give me the path)")
    print("  3  leave it alone")
    choice = input("choice [1/2/3]: ").strip() or "3"

    if choice == "2":
        given = Path(input("path to ffmpeg: ").strip().strip('"'))
        if not given.is_file():
            print("no file there")
            return
        _install_ffmpeg(given)
        check_codecs()
        return

    if choice != "1":
        return

    system, arch = platform.system(), platform.machine()
    url = _ffmpeg_url(system, arch)
    if not url:
        print(f"no download known for {system}/{arch}.")
        print("Build one with qnap/build_ffmpeg.sh, or install your system's package.")
        return
    print(f"downloading {url}")
    print("This is an LGPL build: audio decoders, no video, no patent-encumbered encoders.")
    if input("continue? [y/N] ").strip().lower() != "y":
        return
    _download_ffmpeg(url)
    check_codecs()


def _ffmpeg_url(system: str, arch: str) -> str:
    """Where a build for this machine comes from. Empty when we do not know of one."""
    if system == "Windows":
        return "https://github.com/GyanD/codexffmpeg/releases/latest/download/ffmpeg-release-essentials.zip"
    if system == "Darwin":
        return "https://evermeet.cx/ffmpeg/getrelease/zip"
    if system == "Linux":
        return {
            "x86_64": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
            "aarch64": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz",
            "armv7l": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-armhf-static.tar.xz",
        }.get(arch, "")
    return ""


def _download_ffmpeg(url: str) -> None:
    import tarfile
    import tempfile
    import urllib.request
    import zipfile

    target = ROOT / "runtime" / f"ffmpeg-{platform.machine()}"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg-download"
        urllib.request.urlretrieve(url, archive)
        unpacked = Path(tmp) / "out"
        unpacked.mkdir()
        if zipfile.is_zipfile(archive):
            zipfile.ZipFile(archive).extractall(unpacked)
        else:
            with tarfile.open(archive) as tar:
                tar.extractall(unpacked, filter="data")
        # These archives all bury the binary a few directories down, and the layout
        # differs per publisher, so look for it rather than guessing a path.
        found = next((p for p in unpacked.rglob("ffmpeg*")
                      if p.is_file() and p.name in ("ffmpeg", "ffmpeg.exe")), None)
        if found is None:
            print("the archive had no ffmpeg in it")
            return
        _install_ffmpeg(found)


def _install_ffmpeg(source: Path) -> None:
    target_dir = ROOT / "runtime" / f"ffmpeg-{platform.machine()}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ("ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg")
    shutil.copyfile(source, target)
    target.chmod(0o755)
    if platform.system() == "Darwin":
        # Gatekeeper quarantines anything that came from a browser or a download.
        subprocess.run(["xattr", "-d", "com.apple.quarantine", str(target)],
                       capture_output=True, check=False)
    print(f"installed: {target}")


COMMANDS = {
    "start": start, "stop": stop, "restart": restart, "panel": panel,
    "install": install_service, "uninstall": uninstall_service,
    "getffmpeg": get_ffmpeg,
}


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        return status()
    if command == "codecs":
        return check_codecs()
    handler = COMMANDS.get(command)
    if handler is None:
        print(f"usage: tunethathue [{' | '.join(['status', 'codecs', *COMMANDS])}]")
        return 2
    handler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
