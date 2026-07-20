"""
M3 stub of the hue-entertainment package for the embedded interpreter.

From milestone M3 the C++ host owns all networking (DTLS, HueStream, CLIP),
so the real hue-entertainment library (and its aiohttp/cryptography deps) is
no longer installed. The effects engine imports exactly one runtime symbol
from it - ``LightColorCommand`` (analyzer.py) - and duck-types ``LightChannel``
(``.channel_id`` / ``.name`` / ``.position``). This stub provides both.

Put this file's directory on sys.path INSTEAD of installing the real library.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LightColorCommand:
    """One light's color for one frame - 16-bit components, mirrors models.py."""

    channel_id: int
    red: int
    green: int
    blue: int


@dataclass(slots=True)
class LightChannel:
    """An entertainment-area channel - mirrors the fields the analyzer reads."""

    channel_id: int
    name: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    service_id: str = ""
