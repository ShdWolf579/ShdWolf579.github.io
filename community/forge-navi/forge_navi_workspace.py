#!/usr/bin/env python3
"""Forge-Navi v0.4 workspace and card inventory model.

A workspace is the whole editable set tree, not merely custom/.  The model binds
normal-card scripts to Forge card art and token-script dependencies so Desktop
has one durable record per card.
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
FULL_ART_RE = re.compile(r"^(?P<stem>.+)\.full\.(?P<ext>jpe?g|png|webp)$", re.I)
TOKEN_SCRIPT_RE = re.compile(r"TokenScript\$\s*([A-Za-z0-9_.-]+)")
FORGE_REMOVED_CHARS = {'"', '/', ':', '?'}


@dataclass
class CardRecord:
    name: str
    script_path: str
    types: str = ""
    mana_cost: str = ""
    oracle: str = ""
    status: str = "UNKNOWN"
    expected_art_stem: str = ""
    art_state: str = "MISSING"
    art_path: str = ""
    set_code: str = ""
    token_dependencies: list[str] = field(default_factory=list)
    token_state: str = "NONE"


@dataclass
class WorkspaceInventory:
    workspace_root: str
    custom_root: str
    set_codes: list[str]
    cards: list[CardRecord]
    edition_files: list[str]
    orphan_art: list[str]
    token_scripts: list[str]
    counts: dict[str, int]


def _looks_like_custom(path: Path) -> bool:
    return path.is_dir() and ((path / "cards").is_dir() or (path / "tokens").is_dir())


def discover_workspace_root(path: Path) -> Path:
    """Resolve a user-selected folder/extraction tree to the whole set workspace."""
    path = path.expanduser().resolve()

    if path.name.casefold() == "custom" and _looks_like_custom(path):
        return path.parent

    if (path / "custom").is_dir() and _looks_like_custom(path / "custom"):
        return path

    if _looks_like_custom(path):
        return path.parent

    candidates: list[Path] = []
    try:
        for candidate in path.rglob("custom"):
            if _looks_like_custom(candidate):
                candidates.append(candidate)
    except OSError:
        candidates = []

    if candidates:
        candidates.sort(key=lambda p: (len(p.relative_to(path).parts), str(p).casefold()))
        return candidates[0].parent

    return path


def discover_custom_root(workspace_root: Path) -> Path:
    workspace_root = workspace_root.expanduser().resolve()
    direct = workspace_root / "custom"
    if _looks_like_custom(direct):
        return direct
    if workspace_root.name.casefold() == "custom" and _looks_like_custom(workspace_root):
        return workspace_root

    candidates: list[Path] = []
    try:
        for candidate in workspace_root.rglob("custom"):
            if _looks_like_custom(candidate):
                candidates.append(candidate)
    except OSError:
        candidates = []
    if candidates:
        candidates.sort(key=lambda p: (len(p.relative_to(workspace_root).parts), str(p).casefold()))
        return candidates[0]
    return direct


def safe_extract_workspace_zip(zip_path: Path, destination: Path) -> tuple[Path, Path]:
    zip_path = zip_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError:
                raise ValueError(f"unsafe ZIP member path: {member.filename}")
        zf.extractall(destination)

    workspace_root = discover_workspace_root(destination)
    custom_root = discover_custom_root(workspace_root)
    return workspace_root, custom_root


def save_workspace_zip(workspace_root: Path, out: Path) -> int:
    """Persist the entire editable set workspace, including pics/, editions, etc."""
    workspace_root = workspace_root.expanduser().resolve()
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for path in workspace_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace_root)
        if ".forge-navi" in rel.parts:
            continue
        if path.resolve() == out:
            continue
        files.append(path)

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files, key=lambda p: p.as_posix().casefold()):
            zf.write(path, path.relative_to(workspace_root).as_posix())

    with zipfile.ZipFile(out, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"saved ZIP failed CRC check at {bad}")
    return len(files)


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    normalized = normalized.replace("\u0141", "L").replace("\u0142", "l")
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def forge_image_stem(card_name: str) -> str:
    value = strip_accents(str(card_name))
    return "".join(ch for ch in value if ch not in FORGE_REMOVED_CHARS)


def _parse_fields(path: Path) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields.setdefault(key.strip(), []).append(value.strip())
    return fields


def _first(fields: dict[str, list[str]], key: str) -> str:
    values = fields.get(key)
    return values[0] if values else ""


def _normal_card_scripts(custom_root: Path) -> list[Path]:
    cards = custom_root / "cards"
    if not cards.is_dir():
        return []
    return sorted((p for p in cards.rglob("*.txt") if p.is_file()), key=lambda p: p.as_posix().casefold())


def _token_scripts(custom_root: Path) -> list[Path]:
    tokens = custom_root / "tokens"
    if not tokens.is_dir():
        return []
    return sorted((p for p in tokens.rglob("*.txt") if p.is_file()), key=lambda p: p.as_posix().casefold())


def _card_art_files(workspace_root: Path) -> list[Path]:
    art_root = workspace_root / "pics" / "cards"
    if not art_root.is_dir():
        return []
    return sorted(
        (
            p for p in art_root.rglob("*")
            if p.is_file() and FULL_ART_RE.fullmatch(p.name)
        ),
        key=lambda p: p.as_posix().casefold(),
    )


def build_inventory(
    workspace_root: Path,
    script_statuses: dict[str, str] | None = None,
) -> WorkspaceInventory:
    workspace_root = discover_workspace_root(workspace_root)
    custom_root = discover_custom_root(workspace_root)
    script_statuses = script_statuses or {}

    token_paths = _token_scripts(custom_root)
    token_ids = {p.stem.casefold() for p in token_paths}

    art_paths = _card_art_files(workspace_root)
    art_by_stem: dict[str, list[Path]] = {}
    set_codes: set[str] = set()
    art_root = workspace_root / "pics" / "cards"
    for art in art_paths:
        match = FULL_ART_RE.fullmatch(art.name)
        if not match:
            continue
        art_by_stem.setdefault(match.group("stem").casefold(), []).append(art)
        try:
            rel = art.relative_to(art_root)
            if len(rel.parts) > 1:
                set_codes.add(rel.parts[0])
        except ValueError:
            pass

    matched_art: set[Path] = set()
    cards: list[CardRecord] = []

    for script in _normal_card_scripts(custom_root):
        fields = _parse_fields(script)
        name = _first(fields, "Name") or script.stem
        expected = forge_image_stem(name)
        matches = art_by_stem.get(expected.casefold(), [])

        if len(matches) == 1:
            art_state = "PRESENT"
            art_path = matches[0].relative_to(workspace_root).as_posix()
            matched_art.add(matches[0])
            try:
                rel_art = matches[0].relative_to(art_root)
                set_code = rel_art.parts[0] if len(rel_art.parts) > 1 else ""
            except ValueError:
                set_code = ""
        elif len(matches) > 1:
            art_state = "AMBIGUOUS"
            art_path = "; ".join(p.relative_to(workspace_root).as_posix() for p in matches)
            set_code = ""
            matched_art.update(matches)
        else:
            art_state = "MISSING"
            art_path = ""
            set_code = ""

        raw_values = "\n".join(v for values in fields.values() for v in values)
        dependencies = sorted(set(TOKEN_SCRIPT_RE.findall(raw_values)), key=str.casefold)
        if not dependencies:
            token_state = "NONE"
        else:
            missing = [token for token in dependencies if token.casefold() not in token_ids]
            token_state = "RESOLVED" if not missing else f"MISSING {len(missing)}"

        rel_to_custom = script.relative_to(custom_root).as_posix()
        rel_to_workspace = script.relative_to(workspace_root).as_posix()
        cards.append(
            CardRecord(
                name=name,
                script_path=rel_to_workspace,
                types=_first(fields, "Types"),
                mana_cost=_first(fields, "ManaCost"),
                oracle=_first(fields, "Oracle"),
                status=script_statuses.get(rel_to_custom, "UNKNOWN"),
                expected_art_stem=expected,
                art_state=art_state,
                art_path=art_path,
                set_code=set_code,
                token_dependencies=dependencies,
                token_state=token_state,
            )
        )

    edition_paths: list[Path] = []
    for edition_root in (custom_root / "editions", workspace_root / "editions"):
        if edition_root.is_dir():
            edition_paths.extend(p for p in edition_root.rglob("*.txt") if p.is_file())
    edition_files = sorted(
        {p.relative_to(workspace_root).as_posix() for p in edition_paths},
        key=str.casefold,
    )

    orphan_art = sorted(
        (p.relative_to(workspace_root).as_posix() for p in art_paths if p not in matched_art),
        key=str.casefold,
    )

    counts = {
        "cards": len(cards),
        "green": sum(c.status == "GREEN" for c in cards),
        "errata_green": sum(c.status == "ERRATA-GREEN" for c in cards),
        "yellow": sum(c.status == "YELLOW" for c in cards),
        "red": sum(c.status == "RED" for c in cards),
        "unknown": sum(c.status == "UNKNOWN" for c in cards),
        "art_present": sum(c.art_state == "PRESENT" for c in cards),
        "art_missing": sum(c.art_state == "MISSING" for c in cards),
        "art_ambiguous": sum(c.art_state == "AMBIGUOUS" for c in cards),
        "orphan_art": len(orphan_art),
        "token_scripts": len(token_paths),
        "token_unresolved_cards": sum(c.token_state.startswith("MISSING") for c in cards),
    }

    return WorkspaceInventory(
        workspace_root=str(workspace_root),
        custom_root=str(custom_root),
        set_codes=sorted(set_codes, key=str.casefold),
        cards=cards,
        edition_files=edition_files,
        orphan_art=orphan_art,
        token_scripts=[p.relative_to(workspace_root).as_posix() for p in token_paths],
        counts=counts,
    )
