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


def python_exe(windowed: bool = False) -> str:
    """
    The interpreter to run the daemon with.

    The virtual environment comes first, because that is where the installer put the
    packages. A bare portable interpreter has the right version and none of the
    dependencies, and picking it first is how the service ended up restarting forever
    on ModuleNotFoundError.

    :param windowed: prefer pythonw.exe, so a background thing does not open a console
        window every time Windows starts it.
    """
    for candidate in (
        ROOT / "venv" / "bin" / "python",
        ROOT / "venv" / "Scripts" / "python.exe",
        ROOT / "runtime" / "python" / "bin" / "python3",
        ROOT / "runtime" / "python" / "python.exe",
    ):
        if candidate.is_file():
            if windowed:
                quiet = candidate.with_name(candidate.name.replace("python", "pythonw"))
                if quiet.is_file():
                    return str(quiet)
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
        return _run(["schtasks", "/Query", "/TN", DISPLAY_NAME]) == 0
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
        verb = {"start": "/Run", "stop": "/End"}.get(action)
        if verb:
            subprocess.run(["schtasks", verb, "/TN", DISPLAY_NAME],
                           capture_output=True, check=False)
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
    stopped_service = _service("stop")
    # Even with a service installed, the daemon may have been started by hand - and on
    # Windows ending the scheduled task does nothing to a copy started that way. So look
    # for a loose one either way and finish the job.
    pid = _running_pid()
    if stopped_service and pid is None:
        print("stopped (service)")
        return
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


def _windows_user() -> str:
    """The account the task runs as, with its domain, the way Task Scheduler wants it."""
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _install_windows_task() -> None:
    """
    Start the daemon at sign-in through Task Scheduler.

    Not a Windows service, and that is deliberate. A real service has to answer the
    service control manager within thirty seconds - a handshake a Python process does
    not make - so registering `python.exe` with `sc create` produces a service that
    Windows kills at every start with error 1053. That is what it did.

    Sign-in rather than boot, and as the person rather than as SYSTEM, because that is
    what makes the daemon, the tray and the panel agree about where settings live: as
    SYSTEM the config would sit in a system profile nobody can reach. Nothing here needs
    privileges - it plays no audio and opens no privileged port - so it should not have
    any. A machine that has to run with nobody signed in wants the container or the NAS
    package instead.
    """
    log = state_dir() / "daemon.log"
    command = python_exe(windowed=True)
    arguments = (f'-u "{DAEMON}" --config "{config_path()}" --log "{log}"')
    user = _windows_user()

    # The order of the elements inside <Settings> is fixed by the schema; Task Scheduler
    # rejects the file outright if they are shuffled.
    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{DISPLAY_NAME}: sync Philips Hue lights to whatever is playing</Description>
    <URI>\\{DISPLAY_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>6</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>10</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
    # schtasks reads the definition as UTF-16, and silently fails on anything else.
    xml_file = state_dir() / "task.xml"
    xml_file.write_text(task_xml, encoding="utf-16")

    subprocess.run(["schtasks", "/Create", "/TN", DISPLAY_NAME, "/XML", str(xml_file),
                    "/F"], check=False)
    subprocess.run(["schtasks", "/Run", "/TN", DISPLAY_NAME], capture_output=True,
                   check=False)
    print(f"installed: scheduled task '{DISPLAY_NAME}', starting at sign-in for {user}")


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
        _install_windows_task()
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
        subprocess.run(["schtasks", "/End", "/TN", DISPLAY_NAME],
                       capture_output=True, check=False)
        subprocess.run(["schtasks", "/Delete", "/TN", DISPLAY_NAME, "/F"],
                       capture_output=True, check=False)
        # An older build may have left the service that never worked behind.
        subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True, check=False)
        subprocess.run(["sc", "delete", SERVICE_NAME], capture_output=True, check=False)
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


AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "TuneThatHueTray"


def autostart_on() -> None:
    """
    Start the tray icon when this person signs in.

    Done here rather than from the installer on purpose. The installer runs elevated, so
    anything it writes to the current user's registry belongs to whichever account
    approved the prompt - which on a machine where somebody types an admin password is
    not the person who will actually be using this. Running as the signed-in user puts
    the entry in the right hive.
    """
    if platform.system() != "Windows":
        print("autostart is a Windows thing; elsewhere the service handles it")
        return
    import winreg

    target = f'"{python_exe(windowed=True)}" "{HERE / "tunethathue_tray.py"}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
        winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, target)
    print(f"tray starts at sign-in for {os.environ.get('USERNAME', 'this user')}")


def autostart_off() -> None:
    """Stop starting the tray at sign-in."""
    if platform.system() != "Windows":
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        print("tray no longer starts at sign-in")
    except FileNotFoundError:
        pass


COMMANDS = {
    "start": start, "stop": stop, "restart": restart, "panel": panel,
    "install": install_service, "uninstall": uninstall_service,
    "getffmpeg": get_ffmpeg,
    "autostart": autostart_on, "noautostart": autostart_off,
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
