"""
A stand-in for Music Assistant's provider config, backed by hue-box.toml.

The engine reads its settings through ``StrobeSettings.from_config(cfg)`` and
``PulseSettings.from_config(cfg)``, which only ever call ``cfg.get_value(key)``.
Giving the daemon an object with that one method means the verbatim engine copy
configures itself here exactly as it does inside the provider - no reimplementation
of the settings logic, and a value tuned here means the same thing in MA.

Keys are the provider's own CONF_* strings, so hue-box.toml stays a 1:1 mirror of
what the MA settings screen writes.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

# [effects] holds the base look; the strobe/VFX and pulse blocks mirror the provider's
# own grouping. Anything absent falls back to the engine's own default.
_SECTIONS = ("effects", "strobe", "pulse", "bridge", "sendspin", "snapcast", "slimproto", "dlna", "wled")

# hue-box.toml spells a couple of keys more naturally than the provider's CONF_* value.
# Both are accepted so existing config files keep working.
_ALIASES = {
    "mode": "color_mode",
    # [sendspin] latency_ms is the provider's "Light latency (ms)". Without the
    # section in _SECTIONS above, the alias never fired and the knob did nothing.
    "sendspin_latency_ms": "hue_latency_ms",
    # The file marks percentages with a _pct suffix; the engine's CONF_* keys do not.
    # Without these the values were silently ignored and the engine kept its defaults.
    "pulse_decay_pct": "pulse_decay",
    "pulse_floor_pct": "pulse_floor",
    "strobe_duty_pct": "strobe_duty",
    # [bridge] output/area are the daemon's own runtime state, kept under a prefix so
    # they cannot collide with a provider CONF_* key.
    "bridge_host": "bridge_host",
}


class TomlConfig:
    """Exposes hue-box.toml through the ``get_value`` interface the engine expects."""

    def __init__(self, path: Path | None = None) -> None:
        """Build a config view over ``path`` (missing file = all defaults)."""
        self._values: dict[str, Any] = {}
        if path is not None:
            self.reload(path)

    def reload(self, path: Path) -> None:
        """
        Re-read the toml, flattening the known sections into one key space.

        A missing or broken file leaves the previous values in place and says so on
        stderr: silently falling back to defaults looks exactly like "my settings are
        being ignored", which is the worst failure mode for a tuning box.
        """
        try:
            data = tomllib.load(path.open("rb"))
        except FileNotFoundError:
            print(f"[config] {path} not found - using defaults", file=sys.stderr)
            return
        except (tomllib.TOMLDecodeError, OSError) as err:
            print(f"[config] {path} could not be read ({err}) - keeping previous settings",
                  file=sys.stderr)
            return
        flat: dict[str, Any] = {}
        for section in _SECTIONS:
            block = data.get(section)
            if isinstance(block, dict):
                prefix = "" if section == "effects" else f"{section}_"
                for key, value in block.items():
                    # [strobe] blackout -> strobe_blackout, matching CONF_STROBE_BLACKOUT.
                    name = key if key.startswith(prefix) else f"{prefix}{key}"
                    flat[_ALIASES.get(name, name)] = value
        rotation = data.get("effects", {}).get("rotation")
        if isinstance(rotation, dict):
            flat["palette_rotate"] = rotation.get("enabled", False)
            flat["palette_rotate_list"] = rotation.get("list", [])
            flat["palette_rotate_beats"] = rotation.get("beats", 16)
            flat["palette_rotate_smooth"] = rotation.get("smooth", True)
        per_light = data.get("effects", {}).get("per_light")
        if isinstance(per_light, dict):
            flat["perlight_brightness"] = json.dumps(
                {str(k): v for k, v in per_light.items()}
            )
        self._values = flat

    def get_value(self, key: str) -> Any:
        """Return the stored value for ``key``, or None so the caller's default wins."""
        return self._values.get(key)

    def set_value(self, key: str, value: Any) -> None:
        """Update a value in memory (the WebUI persists separately)."""
        self._values[key] = value

    def as_dict(self) -> dict[str, Any]:
        """Return every known setting, for the WebUI to render."""
        return dict(self._values)
