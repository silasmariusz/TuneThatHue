"""
Check the things that must hold before anything is packaged.

Every one of these checks is here because the matching bug shipped once:

    the example config did not parse   - so no setting in the file took effect, and the
                                         daemon said so in one log line nobody was reading
    the panel's script did not parse   - a stylesheet block had landed inside <script>
    a module did not compile           - a patch had been applied to the wrong place

None of it needs a test framework or a running daemon. Run it before building:

    python tools/preflight.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Where our own code lives. `effects/` is left out on purpose: it is carried
# byte-for-byte from Music Assistant and is not ours to judge.
OUR_CODE = ("python", "install", "tools")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else f'  - {detail}'}")
    if not ok:
        failures.append(name)


print("config")
for toml_file in sorted((ROOT / "config").glob("*.toml")):
    try:
        tomllib.loads(toml_file.read_text(encoding="utf-8"))
        check(toml_file.name, True)
    except (tomllib.TOMLDecodeError, OSError) as err:
        check(toml_file.name, False, str(err))

print("python")
for folder in OUR_CODE:
    for source in sorted((ROOT / folder).rglob("*.py")):
        try:
            ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except SyntaxError as err:
            check(str(source.relative_to(ROOT)), False, f"line {err.lineno}: {err.msg}")
    check(f"{folder}/ compiles", not failures or not any(
        f.startswith(folder) for f in failures))

print("panel")
html = (ROOT / "resources" / "webui.html").read_text(encoding="utf-8")
# The panel is one file, so a stylesheet rule that slips into a script tag takes the
# whole page down. Node checks the script properly when it is around; without it, look
# for the shape of the mistake.
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
check("has a script", bool(scripts))
for index, script in enumerate(scripts):
    stray = re.search(r"^\s*[.#][\w-]+\s*(,[^\n{]*)?\{", script, re.M)
    check(f"script {index + 1} carries no stylesheet",
          stray is None, stray.group(0).strip() if stray else "")

print("manifests")
for manifest in sorted(ROOT.rglob("plugin.json")):
    if "runtime" in manifest.parts or "venv" in manifest.parts:
        continue
    try:
        json.loads(manifest.read_text(encoding="utf-8"))
        check(str(manifest.relative_to(ROOT)), True)
    except (json.JSONDecodeError, OSError) as err:
        check(str(manifest.relative_to(ROOT)), False, str(err))

print()
if failures:
    print(f"{len(failures)} problem(s): " + ", ".join(failures))
    sys.exit(1)
print("all clear")
