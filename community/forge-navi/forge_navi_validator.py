#!/usr/bin/env python3
"""Strict validation for syntax emitted by the public Forge-Navi generator."""
from __future__ import annotations

import re

GENERATED_MARKER = "FORGE-NAVI GENERATED"
ERRATA_GREEN_RE = re.compile(r"FORGE-NAVI\s+(?:STATUS:\s*)?ERRATA-GREEN", re.I)

EFFECT_RULES = {
    "Draw": ({"NumCards"}, {"NumCards", "Defined", "OptionalDecider", "SubAbility", "SpellDescription"}, set()),
    "GainLife": ({"Defined", "LifeAmount"}, {"Defined", "LifeAmount", "SubAbility", "SpellDescription"}, set()),
    "Mill": ({"NumCards"}, {"Defined", "ValidTgts", "NumCards", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "Destroy": (set(), {"Defined", "ValidTgts", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "Discard": ({"NumCards"}, {"Defined", "ValidTgts", "NumCards", "Mode", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "PutCounter": ({"CounterType", "CounterNum"}, {"Defined", "ValidTgts", "CounterType", "CounterNum", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "Scry": ({"ScryNum"}, {"ScryNum", "SubAbility", "SpellDescription"}, set()),
    "Token": ({"TokenScript", "TokenAmount", "TokenOwner"}, {"TokenScript", "TokenAmount", "TokenOwner", "SubAbility", "SpellDescription"}, set()),
}

TRIGGER_RULES = {
    "Phase": ({"Phase", "TriggerZones", "Execute"}, {"Mode", "Phase", "ValidPlayer", "TriggerZones", "Execute"}),
    "SpellCast": ({"ValidCard", "TriggerZones", "Execute"}, {"Mode", "ValidCard", "ValidActivatingPlayer", "TriggerZones", "Execute"}),
}

SAFE_KEYWORDS = {
    "Flying", "First strike", "Double strike", "Deathtouch", "Haste", "Hexproof",
    "Indestructible", "Lifelink", "Menace", "Reach", "Trample", "Vigilance", "Defender",
}


def is_generated(raw_text: str) -> bool:
    return GENERATED_MARKER in raw_text


def is_errata_green(raw_text: str) -> bool:
    return bool(ERRATA_GREEN_RE.search(raw_text))


def _parts(value: str):
    parsed: list[tuple[str, str]] = []
    for raw in value.split("|"):
        part = raw.strip()
        if not part:
            continue
        if "$" not in part:
            return parsed, f"malformed Forge segment '{part}' (expected Key$ Value)"
        key, val = part.split("$", 1)
        key, val = key.strip(), val.strip()
        if not key or not val:
            return parsed, f"malformed Forge segment '{part}'"
        parsed.append((key, val))
    if not parsed:
        return parsed, "empty Forge ability body"
    return parsed, None


def _effect_errors(value: str, label: str) -> list[str]:
    parts, error = _parts(value)
    if error:
        return [f"{label}: {error}"]

    head, api = parts[0]
    if head not in {"SP", "AB", "DB"}:
        return [f"{label}: invalid generated API head '{head}$'"]

    rule = EFFECT_RULES.get(api)
    if rule is None:
        return [f"{label}: unknown generated API '{api}'"]

    required, allowed, selector_choices = rule
    errors: list[str] = []
    params: dict[str, str] = {}
    for key, value in parts[1:]:
        if key in params:
            errors.append(f"{label}: duplicate parameter '{key}$'")
        params[key] = value

    for key in sorted(set(params) - allowed):
        errors.append(f"{label}: parameter '{key}$' is not valid for generated {api}")
    for key in sorted(required - set(params)):
        errors.append(f"{label}: generated {api} is missing required '{key}$'")
    if selector_choices and not (selector_choices & set(params)):
        choices = " or ".join(f"{key}$" for key in sorted(selector_choices))
        errors.append(f"{label}: generated {api} requires {choices}")
    return errors


def _trigger_errors(value: str, label: str) -> list[str]:
    parts, error = _parts(value)
    if error:
        return [f"{label}: {error}"]

    params: dict[str, str] = {}
    errors: list[str] = []
    for key, value in parts:
        if key in params:
            errors.append(f"{label}: duplicate trigger parameter '{key}$'")
        params[key] = value

    mode = params.get("Mode")
    if not mode:
        return errors + [f"{label}: trigger is missing Mode$"]

    rule = TRIGGER_RULES.get(mode)
    if rule is None:
        return errors + [f"{label}: unknown generated trigger mode '{mode}'"]

    required, allowed = rule
    for key in sorted(set(params) - allowed):
        errors.append(f"{label}: parameter '{key}$' is not valid for generated {mode} trigger")
    for key in sorted(required - set(params)):
        errors.append(f"{label}: generated {mode} trigger is missing required '{key}$'")
    return errors


def validate_generated_script(fields: dict[str, list[str]], raw_text: str) -> list[str]:
    if not is_generated(raw_text):
        return []

    errors: list[str] = []
    for index, value in enumerate(fields.get("A", []), 1):
        errors.extend(_effect_errors(value, f"A ability {index}"))
    for index, value in enumerate(fields.get("T", []), 1):
        errors.extend(_trigger_errors(value, f"T trigger {index}"))

    for value in fields.get("SVar", []):
        if ":" not in value:
            continue
        name, body = value.split(":", 1)
        body = body.strip()
        if body.startswith(("SP$", "AB$", "DB$")):
            errors.extend(_effect_errors(body, f"SVar {name.strip()}"))
        else:
            errors.append(
                f"SVar {name.strip()}: unsupported generated SVar head; expected SP$, AB$, or DB$"
            )

    for keyword in fields.get("K", []):
        if keyword not in SAFE_KEYWORDS:
            errors.append(f"generated keyword '{keyword}' is not in the grounded safe list")
    return errors
