"""
Build the settings screen straight from the Music Assistant provider sources.

The panel used to hand-write its own labels and grouping, which drifted from the
provider the moment either side changed. Instead this reads the byte-identical copies
in ``effects/ma_provider/``: the ConfigEntry list is parsed out of ``provider.py`` with
``ast`` (no import, so none of MA's dependencies are needed), and the visible text comes
from the same ``strings.json`` the MA settings screen uses.

The result is that the two screens carry the same keys, the same labels and descriptions,
the same order, the same defaults and the same advanced/hidden split - and stay that way
after every ``tools/sync_effects.py`` run, because both sides read one source.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

PROVIDER_DIR = Path(__file__).resolve().parents[1] / "effects" / "ma_provider"

# Keys whose value the daemon cannot honour: they address Music Assistant itself rather
# than the effects engine. Listed explicitly so anything new shows up instead of being
# silently swallowed.
_NOT_APPLICABLE = {
    "bridge_host",
    "bridge_id",
    "hue_username",
    "hue_clientkey",
    "pair",
    "repair_bridge",
    "pair_intro",
    "status_label",
    "live_preview",
    "perlight_brightness",
}


def load_schema() -> list[dict[str, Any]]:
    """
    Return the provider's config entries, in MA's own order, with MA's own text.

    Each item carries: key, type, label, description, default, range, options,
    advanced, hidden - plus ``applicable`` False for entries this box cannot act on.
    """
    entries = _parse_entries(PROVIDER_DIR / "provider.py")
    strings = _load_strings(PROVIDER_DIR / "strings.json")
    out: list[dict[str, Any]] = []
    for e in entries:
        key = e["key"]
        text = strings.get(key, {})
        e["label"] = text.get("label") or key
        e["description"] = text.get("description") or ""
        e["options"] = _merge_option_titles(e.get("options") or [], text.get("options") or {})
        e["applicable"] = key not in _NOT_APPLICABLE
        out.append(e)
    return out


# -- internals --


def _load_strings(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("config_entries", {})
    except (OSError, ValueError):
        return {}


def _merge_option_titles(
    values: list[tuple[Any, str | None]], titles: dict[str, str]
) -> list[dict[str, Any]]:
    """
    Pair each option with its display title.

    An inline ``title=`` on the option wins (that is how the colour list names its hex
    values); otherwise the translation is used, and the raw value is the last resort.
    """
    out = []
    for value, inline in values:
        out.append({"value": value, "title": inline or titles.get(str(value)) or str(value)})
    return out


def _parse_entries(path: Path) -> list[dict[str, Any]]:
    """Pull every ConfigEntry(...) out of the provider's get_config_entries, in order."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_config_entries":
            func = node
            break
    if func is None:
        return []
    entries: list[dict[str, Any]] = []
    for call in ast.walk(func):
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "ConfigEntry"):
            continue
        entry = _entry_from_call(call)
        if entry:
            entries.append(entry)
    return entries


def _entry_from_call(call: ast.Call) -> dict[str, Any] | None:
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    key = _literal(kw.get("key"))
    if not isinstance(key, str):
        return None
    entry: dict[str, Any] = {
        "key": key,
        "type": _enum_name(kw.get("type")),
        "default": _literal(kw.get("default_value")),
        "range": _literal(kw.get("range")),
        "options": _option_values(kw.get("options")),
        "advanced": bool(_literal(kw.get("advanced"))),
        "hidden": bool(_literal(kw.get("hidden"))),
        "multi": bool(_literal(kw.get("multi_value"))),
    }
    return entry


def _literal(node: ast.AST | None) -> Any:
    """Best-effort constant folding; anything computed at runtime comes back as None."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        # A Name (e.g. DEFAULT_STROBE_COVERAGE) resolves against the engine's constants,
        # which the daemon already imports; the caller fills those in.
        if isinstance(node, ast.Name):
            return _constant(node.id)
        if isinstance(node, ast.Attribute):
            return _enum_name(node)
        return None


def _constant(name: str) -> Any:
    from hue_fx import constants  # noqa: PLC0415 - avoids a cycle at module import

    return getattr(constants, name, None)


def _enum_name(node: ast.AST | None) -> str | None:
    """ConfigEntryType.INTEGER -> "integer"."""
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return None


def _option_values(node: ast.AST | None) -> list[tuple[Any, str | None]]:
    """
    Extract the option values from an options=[...] argument.

    Handles the literal list, ``ConfigValueOption(x, title=...)`` calls, and the
    comprehensions the provider uses over the engine's own option tuples.
    """
    if node is None:
        return []
    values: list[tuple[Any, str | None]] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "ConfigValueOption":
            if sub.args:
                v = _literal(sub.args[0])
                if v is not None:
                    title = next(
                        (_literal(k.value) for k in sub.keywords if k.arg == "title"), None
                    )
                    values.append((v, title if isinstance(title, str) else None))
    if values:
        return values
    comp = _comprehension_options(node)
    if comp:
        return comp
    # A bare comprehension over a constants tuple, e.g. [ConfigValueOption(v) for v, _ in X]
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            const = _constant(sub.id)
            if isinstance(const, (list, tuple)):
                return [
                    (c[0], c[1]) if isinstance(c, (list, tuple)) and len(c) > 1 else (c, None)
                    for c in const
                ]
    return []


def _comprehension_options(node: ast.AST) -> list[tuple[Any, str | None]]:
    """
    Unroll ``[ConfigValueOption(x, title=x.capitalize()) for x in SOME_TUPLE]``.

    The provider builds its mode list that way, and reading the title out of it is the
    difference between the screen saying "Pulse" and saying "pulse".
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.ListComp) or len(sub.generators) != 1:
            continue
        call, gen = sub.elt, sub.generators[0]
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "ConfigValueOption"):
            continue
        if not (isinstance(gen.target, ast.Name) and isinstance(gen.iter, ast.Name)):
            continue
        items = _constant(gen.iter.id)
        if not isinstance(items, (list, tuple)):
            continue
        var = gen.target.id
        title_node = next((k.value for k in call.keywords if k.arg == "title"), None)
        out: list[tuple[Any, str | None]] = []
        for item in items:
            out.append((item, _bound(title_node, var, item)))
        return out
    return []


def _bound(node: ast.AST | None, var: str, item: Any) -> str | None:
    """Evaluate a title expression with the comprehension variable bound to ``item``."""
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id == var:
        return str(item)
    # str methods that take no arguments: .capitalize(), .title(), .upper(), ...
    if (
        isinstance(node, ast.Call)
        and not node.args
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == var
        and isinstance(item, str)
    ):
        method = getattr(str, node.func.attr, None)
        if callable(method) and node.func.attr in {"capitalize", "title", "upper", "lower"}:
            return method(item)
    lit = _literal(node)
    return lit if isinstance(lit, str) else None
