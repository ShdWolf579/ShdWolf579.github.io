#!/usr/bin/env python3
"""Deterministic Forge-Navi Community script generation.

The public generator intentionally emits only a small, grounded subset of Forge syntax.
Unknown Oracle clauses are preserved as REVIEW notes instead of guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

SAFE_KEYWORDS = {
    "flying": "Flying",
    "first strike": "First strike",
    "double strike": "Double strike",
    "deathtouch": "Deathtouch",
    "haste": "Haste",
    "hexproof": "Hexproof",
    "indestructible": "Indestructible",
    "lifelink": "Lifelink",
    "menace": "Menace",
    "reach": "Reach",
    "trample": "Trample",
    "vigilance": "Vigilance",
    "defender": "Defender",
}


@dataclass
class CardSpec:
    name: str
    mana_cost: str
    types: str
    oracle: str
    colors: str = ""
    pt: str = ""
    keywords: str = ""
    token_script: str = ""


@dataclass
class Draft:
    script: str
    proven_patterns: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def _num(value: str) -> str:
    value = value.strip().lower()
    if value.isdigit():
        return value
    if value in NUMBER_WORDS:
        return str(NUMBER_WORDS[value])
    return value


def _clean_oracle(text: str) -> str:
    return " ".join(text.replace("\r", "\n").split())


def slugify(name: str) -> str:
    text = name.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unnamed_card"


def script_relative_path(name: str) -> Path:
    stem = slugify(name)
    first = stem[0] if stem and stem[0].isalnum() else "_"
    return Path("cards") / first / f"{stem}.txt"


@dataclass
class Effect:
    api: str
    params: list[tuple[str, str]]
    pattern: str

    def render(self, head: str, subability: str | None = None, description: str | None = None) -> str:
        parts = [f"{head}$ {self.api}"]
        parts.extend(f"{key}$ {value}" for key, value in self.params)
        if subability:
            parts.append(f"SubAbility$ {subability}")
        if description:
            parts.append(f"SpellDescription$ {description}")
        return " | ".join(parts)


def _parse_effect(clause: str, token_script: str) -> Effect | None:
    text = clause.strip().rstrip(".")
    low = text.casefold()

    m = re.fullmatch(r"draw (a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?", low)
    if m:
        return Effect("Draw", [("NumCards", _num(m.group(1)))], "DRAW_EFFECT")

    m = re.fullmatch(r"you gain (one|two|three|four|five|six|seven|eight|nine|ten|\d+) life", low)
    if m:
        return Effect("GainLife", [("Defined", "You"), ("LifeAmount", _num(m.group(1)))], "GAIN_LIFE_EFFECT")

    m = re.fullmatch(r"you mill (one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?", low)
    if m:
        return Effect("Mill", [("Defined", "You"), ("NumCards", _num(m.group(1)))], "MILL_EFFECT")

    m = re.fullmatch(r"target player mills? (one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?", low)
    if m:
        return Effect("Mill", [("ValidTgts", "Player"), ("NumCards", _num(m.group(1)))], "MILL_EFFECT")

    m = re.fullmatch(r"destroy target (creature|artifact|enchantment|permanent)", low)
    if m:
        selectors = {
            "creature": "Creature",
            "artifact": "Artifact",
            "enchantment": "Enchantment",
            "permanent": "Permanent",
        }
        return Effect("Destroy", [("ValidTgts", selectors[m.group(1)])], "DESTROY_EFFECT")

    m = re.fullmatch(r"target player discards? (a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?", low)
    if m:
        return Effect(
            "Discard",
            [("ValidTgts", "Player"), ("NumCards", _num(m.group(1))), ("Mode", "TgtChoose")],
            "DISCARD_EFFECT",
        )

    m = re.fullmatch(
        r"put (a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+) \+1/\+1 counters? on target creature",
        low,
    )
    if m:
        return Effect(
            "PutCounter",
            [("ValidTgts", "Creature"), ("CounterType", "P1P1"), ("CounterNum", _num(m.group(1)))],
            "PUT_COUNTER_EFFECT",
        )

    m = re.fullmatch(r"scry (one|two|three|four|five|six|seven|eight|nine|ten|\d+)", low)
    if m:
        return Effect("Scry", [("ScryNum", _num(m.group(1)))], "SCRY_EFFECT")

    m = re.fullmatch(
        r"create (a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+) .+ tokens?",
        low,
    )
    if m and token_script.strip():
        return Effect(
            "Token",
            [("TokenScript", token_script.strip()), ("TokenAmount", _num(m.group(1))), ("TokenOwner", "You")],
            "TOKEN_FIELDS",
        )

    return None


def _split_effects(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip()]


def _render_chain(effects: list[Effect], prefix: str, top_head: str, description: str | None = None) -> list[str]:
    lines: list[str] = []
    for idx, effect in enumerate(effects, 1):
        next_name = f"{prefix}{idx + 1}" if idx < len(effects) else None
        if idx == 1 and top_head in {"SP", "AB"}:
            lines.append(
                "A:" + effect.render(
                    top_head,
                    subability=next_name,
                    description=description,
                )
            )
        else:
            name = f"{prefix}{idx}"
            lines.append(f"SVar:{name}:" + effect.render("DB", subability=next_name))
    return lines


def _compile_simple_spell(oracle: str, token_script: str):
    clauses = _split_effects(oracle)
    effects: list[Effect] = []
    review: list[str] = []
    for clause in clauses:
        effect = _parse_effect(clause, token_script)
        if effect:
            effects.append(effect)
        else:
            review.append(clause)
    if not effects:
        return [], [], review
    return _render_chain(effects, "DBEffect", "SP", oracle), [e.pattern for e in effects], review


def _trigger_payload(body: str, token_script: str, prefix: str):
    clauses = _split_effects(body)
    effects: list[Effect] = []
    review: list[str] = []
    for clause in clauses:
        effect = _parse_effect(clause, token_script)
        if effect:
            effects.append(effect)
        else:
            review.append(clause)
    if not effects:
        return [], [], review

    lines: list[str] = []
    for idx, effect in enumerate(effects, 1):
        name = prefix if idx == 1 else f"{prefix}{idx}"
        next_name = f"{prefix}{idx + 1}" if idx < len(effects) else None
        lines.append(f"SVar:{name}:" + effect.render("DB", subability=next_name))
    return lines, [e.pattern for e in effects], review


def _compile_trigger(sentence: str, token_script: str, trigger_index: int):
    raw = sentence.strip().rstrip(".")
    prefix = f"TrigEffect{trigger_index}"

    m = re.fullmatch(r"at the beginning of your upkeep,\s*(.+)", raw, flags=re.I)
    if m:
        payload, patterns, review = _trigger_payload(m.group(1), token_script, prefix)
        if not payload:
            return [], [], review or [sentence]
        line = (
            f"T:Mode$ Phase | Phase$ Upkeep | ValidPlayer$ You | TriggerZones$ Battlefield | Execute$ {prefix}"
        )
        return [line, *payload], ["PHASE_TRIGGER", *patterns], review

    m = re.fullmatch(r"at the beginning of your end step,\s*(.+)", raw, flags=re.I)
    if m:
        payload, patterns, review = _trigger_payload(m.group(1), token_script, prefix)
        if not payload:
            return [], [], review or [sentence]
        line = (
            f"T:Mode$ Phase | Phase$ End of Turn | ValidPlayer$ You | TriggerZones$ Battlefield | Execute$ {prefix}"
        )
        return [line, *payload], ["PHASE_TRIGGER", *patterns], review

    m = re.fullmatch(r"whenever you cast an instant or sorcery spell,\s*(.+)", raw, flags=re.I)
    if m:
        payload, patterns, review = _trigger_payload(m.group(1), token_script, prefix)
        if not payload:
            return [], [], review or [sentence]
        line = (
            f"T:Mode$ SpellCast | ValidCard$ Instant,Sorcery | ValidActivatingPlayer$ You | "
            f"TriggerZones$ Battlefield | Execute$ {prefix}"
        )
        return [line, *payload], ["SPELL_CAST_TRIGGER", *patterns], review

    return None


def compile_card(spec: CardSpec) -> Draft:
    name = spec.name.strip()
    types = spec.types.strip()
    oracle = _clean_oracle(spec.oracle)
    review: list[str] = []
    patterns: list[str] = []

    if not name:
        review.append("Name is required.")
    if not types:
        review.append("Types is required.")
    if not oracle:
        review.append("Oracle text is required.")

    lines: list[str] = []
    if name:
        lines.append(f"Name:{name}")
    if spec.mana_cost.strip():
        lines.append(f"ManaCost:{spec.mana_cost.strip()}")
    if spec.colors.strip():
        lines.append(f"Colors:{spec.colors.strip()}")
    if types:
        lines.append(f"Types:{types}")
    if spec.pt.strip():
        lines.append(f"PT:{spec.pt.strip()}")

    for raw_kw in re.split(r"[,;\n]+", spec.keywords):
        kw = raw_kw.strip()
        if not kw:
            continue
        canonical = SAFE_KEYWORDS.get(kw.casefold())
        if canonical:
            lines.append(f"K:{canonical}")
            patterns.append("SAFE_NATIVE_KEYWORD")
        else:
            review.append(f"Keyword not in the public safe list: {kw}")

    ability_lines: list[str] = []
    if oracle:
        is_spell = bool(re.search(r"\b(?:Instant|Sorcery)\b", types, flags=re.I))
        if is_spell:
            compiled, used, unresolved = _compile_simple_spell(oracle, spec.token_script)
            ability_lines.extend(compiled)
            patterns.extend(used)
            review.extend(unresolved)
        else:
            sentences = _split_effects(oracle)
            trigger_count = 0
            for sentence in sentences:
                trigger_count += 1
                compiled = _compile_trigger(sentence, spec.token_script, trigger_count)
                if compiled is None:
                    review.append(sentence)
                    continue
                got_lines, used, unresolved = compiled
                ability_lines.extend(got_lines)
                patterns.extend(used)
                review.extend(unresolved)

    lines.extend(ability_lines)

    if oracle:
        lines.append(f"Oracle:{oracle}")

    unique_review: list[str] = []
    for item in review:
        item = item.strip()
        if item and item not in unique_review:
            unique_review.append(item)

    if unique_review:
        notes = [f"# FORGE-NAVI REVIEW: {item}" for item in unique_review]
        oracle_line = lines.pop() if lines and lines[-1].startswith("Oracle:") else None
        lines.extend(notes)
        if oracle_line:
            lines.append(oracle_line)

    unique_patterns: list[str] = []
    for key in patterns:
        if key not in unique_patterns:
            unique_patterns.append(key)

    return Draft(script="\n".join(lines).rstrip() + "\n", proven_patterns=unique_patterns, review=unique_review)
