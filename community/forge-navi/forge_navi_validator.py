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


# Current Forge ApiType enum snapshot from Card-Forge/forge.
# This is syntax authority for ability API names, not semantic proof that a card
# implements its Oracle text correctly.
KNOWN_API_TYPES = {
    "Abandon", "ActivateAbility", "AddOrRemoveCounter", "AddPhase", "AddTurn",
    "AdvanceCrank", "Airbend", "AlterAttribute", "Amass", "Animate", "AnimateAll",
    "Attach", "AssembleContraption", "AssignGroup", "Balance", "BecomeMonarch",
    "BecomesBlocked", "BidLife", "Blight", "Block", "Bond", "Branch", "Camouflage",
    "ChangeCombatants", "ChangeSpeed", "ChangeTargets", "ChangeText", "ChangeX",
    "ChangeZone", "ChangeZoneAll", "ChaosEnsues", "Charm", "ChooseCard",
    "ChooseColor", "ChooseDirection", "ChooseEvenOdd", "ChooseNumber", "ChoosePlayer",
    "ChooseSector", "ChooseSource", "ChooseType", "ClaimThePrize", "Clash",
    "ClassLevelUp", "Cleanup", "Cloak", "Clone", "Connive", "CopyPermanent",
    "CopySpellAbility", "ControlSpell", "ControlPlayer", "Counter", "DamageAll",
    "DealDamage", "Detain", "DayTime", "Debuff", "DelayedTrigger", "Destroy",
    "DestroyAll", "Dig", "DigMultiple", "DigUntil", "Discard", "Discover",
    "DrainMana", "Draft", "Draw", "EachDamage", "Earthbend", "Effect", "Empower",
    "Encode", "EndCombatPhase", "EndTurn", "Endure", "ExchangeLife",
    "ExchangeLifeVariant", "ExchangeControl", "ExchangeControlVariant",
    "ExchangePower", "ExchangeZone", "ExchangeTextBox", "Explore", "Fight",
    "FlipCoin", "FlipOntoBattlefield", "Fog", "GainControl", "GainControlVariant",
    "GainLife", "GainOwnership", "GameDrawn", "GenericChoice", "Goad", "Haunt",
    "HealDamage", "Heist", "Investigate", "Intensify", "ImmediateTrigger",
    "Incubate", "Learn", "LookAt", "LoseLife", "LosePerpetual", "LosesGame",
    "MakeCard", "Mana", "ManaReflected", "Manifest", "ManifestDread", "Meld",
    "Mill", "MoveCounter", "MultiplePiles", "MultiplyCounter", "MustBlock",
    "Mutate", "NameCard", "OpenAttraction", "PeekAndReveal", "PermanentCreature",
    "PermanentNoncreature", "Phases", "Planeswalk", "Play", "PlayLandVariant",
    "Poison", "PreventDamage", "Proliferate", "Protection", "ProtectionAll",
    "Pump", "PumpAll", "PutCounter", "PutCounterAll", "PutSticker", "Radiation",
    "Recruit", "RearrangeTopOfLibrary", "Regenerate", "Regeneration",
    "RemoveCounter", "RemoveCounterAll", "RemoveFromCombat", "RemoveFromGame",
    "RemoveFromMatch", "ReorderZone", "Repeat", "RepeatEach", "ReplaceCounter",
    "ReplaceEffect", "ReplaceMana", "ReplaceDamage", "ReplaceToken",
    "ReplaceSplitDamage", "RestartGame", "Reveal", "RevealHand",
    "ReverseTurnOrder", "RingTemptsYou", "RollDice", "RollPlanarDice", "RunChaos",
    "Sacrifice", "SacrificeAll", "Scry", "Seek", "SetInMotion", "SetLife",
    "SetState", "Shuffle", "SkipPhase", "SkipTurn", "StoreSVar", "Subgame",
    "Surveil", "SwitchBlock", "TakeInitiative", "Tap", "TapAll", "TapOrUntap",
    "TapOrUntapAll", "TimeTravel", "Token", "TwoPiles", "Unattach", "UnlockDoor",
    "Untap", "UntapAll", "Venture", "VillainousChoice", "Vote", "WinsGame",
    "BlankLine", "DamageResolve", "ChangeZoneResolve", "CompanionChoose",
    "InternalLegendaryRule", "InternalIgnoreEffect", "InternalRadiation",
}

KNOWN_API_TYPES_CASEFOLD = {name.casefold(): name for name in KNOWN_API_TYPES}


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


def _api_head_error(value: str, label: str) -> str | None:
    parts, error = _parts(value)
    if error:
        return None
    head, api = parts[0]
    if head not in {"SP", "AB", "DB"}:
        return None
    if api.casefold() not in KNOWN_API_TYPES_CASEFOLD:
        return f"{label}: Forge ApiType '{api}' does not exist in current Forge"
    return None


def validate_known_api_heads(fields: dict[str, list[str]]) -> list[str]:
    """Validate ability API names for every script, regardless of provenance."""
    errors: list[str] = []

    for index, value in enumerate(fields.get("A", []), 1):
        error = _api_head_error(value, f"A ability {index}")
        if error:
            errors.append(error)

    for value in fields.get("SVar", []):
        if ":" not in value:
            continue
        name, body = value.split(":", 1)
        body = body.strip()
        if body.startswith(("SP$", "AB$", "DB$")):
            error = _api_head_error(body, f"SVar {name.strip()}")
            if error:
                errors.append(error)

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
