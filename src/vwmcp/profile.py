"""The user's profile: how Keystone works for this user.

Keystone serves many users with different habits, so its working rules are
settings, not fixed behaviour. Every setting has a default that suits most
people; the user changes them with the `vw2d_profile` tool (stored in
~/.vwmcp/profile.json). Above all of it: an explicit request in the chat
always beats the profile.

The server instructions are built from the profile at start
(`instructions_block`), and `vw2d_profile` returns the updated text when the
user changes something, so the change applies at once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# key -> definition. "choices": allowed values (each with its meaning for
# Claude, in English, and for the user, in German); "type": number/text list.
SETTINGS: dict[str, dict[str, Any]] = {
    "detail": {
        "default": "keep",
        "de": "Detailtreue beim Nachzeichnen und Bereinigen",
        "choices": {
            "keep": ("When tracing or cleaning up, keep every edge and step of the source. Simplify only "
                     "when the user asks for it - then do it.",
                     "Jede Kante behalten; vereinfachen nur, wenn ich darum bitte"),
            "simplify_ok": ("You may simplify organic or over-detailed shapes (curtains, plants, terrain, "
                            "Make2D fragments) within the simplify tolerance when it helps the drawing; "
                            "show before/after.",
                            "Organisches und Überdetailliertes darf vereinfacht werden (mit Vorher-Nachher)"),
        },
    },
    "merge_parts": {
        "default": "ask",
        "de": "Zusammengehörige Teile (ein Ring, eine Decke) zu einer Fläche vereinigen",
        "choices": {
            "always": ("Parts that belong together (one ring, one slab) become ONE closed area "
                       "(vw2d_boolean union).", "Immer zu einer Fläche vereinigen"),
            "ask": ("Parts that belong together may be one area; propose the union, do it when the user "
                    "agrees or asked for it.", "Vorschlagen, auf Zustimmung warten"),
            "never": ("Keep parts as they are; don't union them unless asked.", "Nie von selbst vereinigen"),
        },
    },
    "areas": {
        "default": "seamless",
        "de": "Benachbarte Flächen",
        "choices": {
            "seamless": ("Adjacent areas share their edges exactly: no gaps, no overlaps, no stacked fills, "
                         "no double lines.", "Lückenlos, ohne Überlappung, keine Doppellinien"),
            "loose": ("Overlapping fills and outlines on fills are fine in this user's drawings (e.g. a "
                      "fill below its outline); don't fix them unless asked.",
                      "Überlagerungen sind in Ordnung (z. B. Füllfläche unter Umriss)"),
        },
    },
    "curves": {
        "default": "true_arcs",
        "de": "Bögen und Kurven",
        "choices": {
            "true_arcs": ("Draw arcs as true arcs (path segments with arc_to), never as segmented polylines. "
                          "Organic curves from imports may stay polylines.", "Bögen als echte Bögen"),
            "polylines_ok": ("Arcs may be drawn as polylines when that is simpler.",
                             "Bögen dürfen Polylinien sein"),
        },
    },
    "classes": {
        "default": "always",
        "de": "Klassen",
        "choices": {
            "always": ("Every object goes into a meaningful class (never the none class); create missing "
                       "classes with vw2d_setup.", "Jedes Objekt in eine passende Klasse"),
            "keep_import": ("Leave imported objects in the classes they came with (e.g. the DWG layers); "
                            "only new objects need a class.", "Importierte Klassen so lassen"),
        },
    },
    "attributes": {
        "default": "by_class",
        "de": "Grafische Attribute",
        "choices": {
            "by_class": ("Graphic attributes come from the class; explicit attributes only when needed.",
                         "Nach Klasse"),
            "explicit_ok": ("Explicit attributes on objects are fine; don't convert them to by-class unless "
                            "asked.", "Eigene Attribute am Objekt sind in Ordnung"),
        },
    },
    "check_mode": {
        "default": "auto",
        "de": "Prüfen und Reparieren",
        "choices": {
            "auto": ("New drawing work: vw2d_check, then vw2d_fix until no errors remain. Imported or "
                     "generated drawings (Make2D, VectorView, sections, elevations, DWG): check with "
                     "preset 'drawing', report, and fix only what the user wants - their pieces, outlines "
                     "on fills and gaps between fragments are normal there.",
                     "Neue Zeichnungen bis fehlerfrei; Importe/Schnitte nur berichten"),
            "strict": ("Always vw2d_check and vw2d_fix until no errors remain, also on imports.",
                       "Immer bis fehlerfrei reparieren"),
            "report": ("Check and report; fix only what the user asks for.", "Nur berichten"),
        },
    },
    "confirm_above": {
        "default": 200,
        "type": "int",
        "min": 0,
        "max": 100000,
        "de": "Ab so vielen geänderten Objekten erst an einem Ausschnitt zeigen und nachfragen (0 = nie)",
        "en": ("A change that touches more than {v} objects: try it on a part first (bbox), show "
               "before/after (vw2d_preview marks=false) and wait for the user's OK."),
        "en_off": "Large changes need no extra confirmation.",
    },
    "tolerance_mm": {
        "default": 1.0,
        "type": "float",
        "min": 0.001,
        "max": 1000.0,
        "de": "Fangtoleranz beim Prüfen (mm)",
        "en": "Checks and fixes use tolerance_mm={v} unless the user says otherwise.",
    },
    "simplify_paper_mm": {
        "default": 0.1,
        "type": "float",
        "min": 0.001,
        "max": 10.0,
        "de": "Vereinfachen: erlaubte Abweichung auf dem Papier (mm, mal Ebenenmaßstab)",
        "en": ("Simplifying allows {v} mm deviation on paper (times the layer scale: {v} mm at 1:100 = "
               "{v100} mm real); pass a different tolerance when the user asks."),
    },
    "rules": {
        "default": [],
        "type": "list",
        "de": "Eigene Regeln (frei formuliert, gelten immer)",
        "en": "The user's own rules (always follow them):",
    },
}

ORDER = list(SETTINGS)


class ProfileError(ValueError):
    pass


def _home() -> Path:
    return Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))


def path() -> Path:
    return _home() / "profile.json"


def defaults() -> dict[str, Any]:
    return {k: (list(d["default"]) if isinstance(d["default"], list) else d["default"]) for k, d in SETTINGS.items()}


def _stored() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict[str, Any]:
    """Current profile: stored values over the defaults (invalid stored values are ignored)."""
    out = defaults()
    for k, v in _stored().items():
        if k in SETTINGS:
            try:
                out[k] = validate(k, v)
            except ProfileError:
                pass
    return out


def validate(key: str, value: Any) -> Any:
    d = SETTINGS.get(key)
    if d is None:
        raise ProfileError("unknown setting %r (settings: %s)" % (key, ", ".join(ORDER)))
    if "choices" in d:
        if value not in d["choices"]:
            raise ProfileError("%s must be one of %s" % (key, ", ".join(d["choices"])))
        return value
    t = d["type"]
    if t == "list":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
            raise ProfileError("%s must be a list of texts" % key)
        return [s.strip() for s in value]
    try:
        num = int(value) if t == "int" else float(value)
    except (TypeError, ValueError):
        raise ProfileError("%s must be a number" % key) from None
    if isinstance(value, bool) or not (d["min"] <= num <= d["max"]):
        raise ProfileError("%s must be between %g and %g" % (key, d["min"], d["max"]))
    return num


def save(changes: dict[str, Any]) -> dict[str, Any]:
    """Validate and store changes (only keys that differ from the default are written)."""
    prof = load()
    for k, v in changes.items():
        prof[k] = validate(k, v)
    stored = {k: v for k, v in prof.items() if v != SETTINGS[k]["default"]}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return prof


def add_rule(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ProfileError("the rule is empty")
    prof = load()
    if text in prof["rules"]:
        return prof
    return save({"rules": prof["rules"] + [text]})


def remove_rule(index: int) -> dict[str, Any]:
    prof = load()
    rules = list(prof["rules"])
    if not 1 <= index <= len(rules):
        raise ProfileError("no rule %d (there are %d)" % (index, len(rules)))
    del rules[index - 1]
    return save({"rules": rules})


def reset(keys: list[str] | None = None) -> dict[str, Any]:
    if not keys:
        p = path()
        if p.exists():
            p.unlink()
        return load()
    stored = _stored()
    for k in keys:
        if k not in SETTINGS:
            raise ProfileError("unknown setting %r" % k)
        stored.pop(k, None)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    return load()


def _g(x) -> str:
    return "%g" % x


def rule_text(key: str, value: Any) -> str | None:
    """The instruction line for one setting (English, for Claude). The
    extension launcher builds the same text from table() (mcpb/server/profile.js)."""
    d = SETTINGS[key]
    if "choices" in d:
        return d["choices"][value][0]
    if d["type"] == "list":
        if not value:
            return None
        return d["en"] + "".join("\n  %d. %s" % (i + 1, r) for i, r in enumerate(value))
    if not value and "en_off" in d:
        return d["en_off"]
    return d["en"].replace("{v100}", _g(value * 100)).replace("{v}", _g(value))


def table() -> dict[str, Any]:
    """Everything the launcher needs to build the profile block itself (static.json)."""
    out = {}
    for k in ORDER:
        d = SETTINGS[k]
        e: dict[str, Any] = {"default": d["default"]}
        if "choices" in d:
            e["choices"] = {c: v[0] for c, v in d["choices"].items()}
        else:
            e["type"] = d["type"]
            e["en"] = d["en"]
            for x in ("en_off", "min", "max"):
                if x in d:
                    e[x] = d[x]
        out[k] = e
    return {"order": list(ORDER), "settings": out}


def instructions_block(prof: dict[str, Any] | None = None) -> str:
    prof = load() if prof is None else prof
    lines = [rule_text(k, prof[k]) for k in ORDER]
    return "\n".join("- " + ln for ln in lines if ln)


def describe(prof: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """For the user: every setting with its current value, default and choices (German)."""
    prof = load() if prof is None else prof
    out = []
    for k in ORDER:
        d = SETTINGS[k]
        row: dict[str, Any] = {"key": k, "name": d["de"], "value": prof[k], "default": d["default"],
                               "changed": prof[k] != d["default"]}
        if "choices" in d:
            row["choices"] = {c: v[1] for c, v in d["choices"].items()}
            row["meaning"] = d["choices"][prof[k]][1]
        out.append(row)
    return out
