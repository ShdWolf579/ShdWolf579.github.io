#!/usr/bin/env python3
"""Forge-Navi Community Core v0.3-dev.

Structural QA, ZIP/project-root discovery, deterministic script generation,
token handoff, and gated packaging for Forge custom content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from forge_navi_scripter import CardSpec, compile_card, script_relative_path

SVAR_REF_RE = re.compile(r"(?:Execute\$|SubAbility\$|References\$)\s*([A-Za-z0-9_]+)")
TOKEN_SCRIPT_RE = re.compile(r"TokenScript\$\s*([A-Za-z0-9_.-]+)")
REVIEW_MARKER_RE = re.compile(r"(?:TODO|FIXME|FORGE-NAVI REVIEW)", re.I)
ERRATA_GREEN_MARKER_RE = re.compile(r"FORGE-NAVI\s+(?:STATUS:\s*)?ERRATA-GREEN", re.I)
GENERATED_MARKER = "FORGE-NAVI GENERATED"

GENERATED_EFFECT_RULES = {
    "Draw": ({"NumCards"}, {"NumCards", "Defined", "OptionalDecider", "SubAbility", "SpellDescription"}, set()),
    "GainLife": ({"Defined", "LifeAmount"}, {"Defined", "LifeAmount", "SubAbility", "SpellDescription"}, set()),
    "Mill": ({"NumCards"}, {"Defined", "ValidTgts", "NumCards", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "Destroy": (set(), {"Defined", "ValidTgts", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "Discard": ({"NumCards"}, {"Defined", "ValidTgts", "NumCards", "Mode", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "PutCounter": ({"CounterType", "CounterNum"}, {"Defined", "ValidTgts", "CounterType", "CounterNum", "SubAbility", "SpellDescription"}, {"Defined", "ValidTgts"}),
    "Scry": ({"ScryNum"}, {"ScryNum", "SubAbility", "SpellDescription"}, set()),
    "Token": ({"TokenScript", "TokenAmount", "TokenOwner"}, {"TokenScript", "TokenAmount", "TokenOwner", "SubAbility", "SpellDescription"}, set()),
}

GENERATED_TRIGGER_RULES = {
    "Phase": ({"Phase", "TriggerZones", "Execute"}, {"Mode", "Phase", "ValidPlayer", "TriggerZones", "Execute"}),
    "SpellCast": ({"ValidCard", "TriggerZones", "Execute"}, {"Mode", "ValidCard", "ValidActivatingPlayer", "TriggerZones", "Execute"}),
}

SAFE_GENERATED_KEYWORDS = {
    "Flying", "First strike", "Double strike", "Deathtouch", "Haste", "Hexproof",
    "Indestructible", "Lifelink", "Menace", "Reach", "Trample", "Vigilance", "Defender",
}


@dataclass
class Finding:
    severity: str
    file: str
    message: str


def _looks_like_custom_root(path: Path) -> bool:
    return path.is_dir() and ((path / "cards").is_dir() or (path / "tokens").is_dir())


def normalize_root(root: Path) -> Path:
    """Find a Forge custom root from a direct root, wrapper folder, or extracted ZIP."""
    root = root.expanduser().resolve()
    if _looks_like_custom_root(root):
        return root

    direct = root / "custom"
    if _looks_like_custom_root(direct):
        return direct

    candidates: list[Path] = []
    try:
        for candidate in root.rglob("custom"):
            if _looks_like_custom_root(candidate):
                candidates.append(candidate)
    except OSError:
        candidates = []

    if candidates:
        candidates.sort(key=lambda p: (len(p.relative_to(root).parts), str(p).casefold()))
        return candidates[0]

    return root


def safe_extract_zip(zip_path: Path, destination: Path) -> Path:
    """Extract a ZIP while rejecting path traversal, then return the detected custom root."""
    zip_path = zip_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = destination / member.filename
            try:
                resolved = member_path.resolve()
                resolved.relative_to(destination)
            except (OSError, ValueError):
                raise ValueError(f"unsafe ZIP member path: {member.filename}")
        zf.extractall(destination)

    return normalize_root(destination)


def script_roots(root: Path) -> list[Path]:
    root = normalize_root(root)
    roots = []
    for name in ("cards", "tokens"):
        candidate = root / name
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def iter_script_files(root: Path) -> Iterable[Path]:
    for script_root in script_roots(root):
        for path in sorted(script_root.rglob("*.txt")):
            if path.is_file():
                yield path


def display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def parse_script(path: Path, root: Path | None = None) -> tuple[dict[str, list[str]], list[Finding]]:
    fields: dict[str, list[str]] = {}
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    shown = display_path(path, root) if root else str(path)

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if REVIEW_MARKER_RE.search(line):
                findings.append(Finding("WARN", shown, f"line {lineno}: {line.lstrip('#').strip()}"))
            continue
        if ":" not in line:
            findings.append(Finding("WARN", shown, f"line {lineno}: no ':' separator"))
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        fields.setdefault(key, []).append(value)

    return fields, findings


def first(fields: dict[str, list[str]], key: str) -> str | None:
    values = fields.get(key)
    return values[0] if values else None


def _parse_dollar_parts(value: str) -> tuple[list[tuple[str, str]], str | None]:
    parts: list[tuple[str, str]] = []
    for raw_part in value.split("|"):
        part = raw_part.strip()
        if not part:
            continue
        if "$" not in part:
            return parts, f"malformed Forge segment '{part}' (expected Key$ Value)"
        key, val = part.split("$", 1)
        key, val = key.strip(), val.strip()
        if not key or not val:
            return parts, f"malformed Forge segment '{part}'"
        parts.append((key, val))
    if not parts:
        return parts, "empty Forge ability body"
    return parts, None


def _validate_generated_effect(value: str, rel: str, label: str) -> list[Finding]:
    parts, error = _parse_dollar_parts(value)
    if error:
        return [Finding("ERROR", rel, f"{label}: {error}")]

    findings: list[Finding] = []
    head, api = parts[0]
    if head not in {"SP", "AB", "DB"}:
        return [Finding("ERROR", rel, f"{label}: invalid generated API head '{head}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    errata_green_files: set[str] = set()
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        generated_strict = GENERATED_MARKER in raw_text
        if ERRATA_GREEN_MARKER_RE.search(raw_text):
            errata_green_files.add(rel)
        findings.extend(validate_generated_syntax(fields, raw_text, rel))
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                severity = "ERROR" if generated_strict else "WARN"
                findings.append(Finding(severity, rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    script_statuses: dict[str, str] = {}
    for file_name in script_files:
        file_findings = [f for f in findings if f.file == file_name]
        if any(f.severity == "ERROR" for f in file_findings):
            script_statuses[file_name] = "RED"
        elif any(f.severity == "WARN" for f in file_findings):
            script_statuses[file_name] = "YELLOW"
        elif file_name in errata_green_files:
            script_statuses[file_name] = "ERRATA-GREEN"
        else:
            script_statuses[file_name] = "GREEN"

    summary = {
        "root": str(root),
        "script_files": script_files,
        "script_statuses": script_statuses,
        "scripts_scanned": scanned,
        "green": sum(status == "GREEN" for status in script_statuses.values()),
        "errata_green": sum(status == "ERRATA-GREEN" for status in script_statuses.values()),
        "red_scripts": sum(status == "RED" for status in script_statuses.values()),
        "yellow_scripts": sum(status == "YELLOW" for status in script_statuses.values()),
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
")]

    rule = GENERATED_EFFECT_RULES.get(api)
    if rule is None:
        return [Finding("ERROR", rel, f"{label}: unknown generated API '{api}'")]

    required, allowed, selector_choices = rule
    params: dict[str, str] = {}
    for key, val in parts[1:]:
        if key in params:
            findings.append(Finding("ERROR", rel, f"{label}: duplicate parameter '{key}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"))
        params[key] = val

    for key in sorted(set(params) - allowed):
        findings.append(Finding("ERROR", rel, f"{label}: parameter '{key}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
 is not valid for generated {api}"))

    for key in sorted(required - set(params)):
        findings.append(Finding("ERROR", rel, f"{label}: generated {api} is missing required '{key}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"))

    if selector_choices and not (selector_choices & set(params)):
        choices = " or ".join(f"{key}$" for key in sorted(selector_choices))
        findings.append(Finding("ERROR", rel, f"{label}: generated {api} requires {choices}"))

    return findings


def _validate_generated_trigger(value: str, rel: str, label: str) -> list[Finding]:
    parts, error = _parse_dollar_parts(value)
    if error:
        return [Finding("ERROR", rel, f"{label}: {error}")]

    findings: list[Finding] = []
    params: dict[str, str] = {}
    for key, val in parts:
        if key in params:
            findings.append(Finding("ERROR", rel, f"{label}: duplicate trigger parameter '{key}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"))
        params[key] = val

    mode = params.get("Mode")
    if not mode:
        return findings + [Finding("ERROR", rel, f"{label}: trigger is missing Mode$")]

    rule = GENERATED_TRIGGER_RULES.get(mode)
    if rule is None:
        return findings + [Finding("ERROR", rel, f"{label}: unknown generated trigger mode '{mode}'")]

    required, allowed = rule
    for key in sorted(set(params) - allowed):
        findings.append(Finding("ERROR", rel, f"{label}: parameter '{key}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
 is not valid for generated {mode} trigger"))
    for key in sorted(required - set(params)):
        findings.append(Finding("ERROR", rel, f"{label}: generated {mode} trigger is missing required '{key}def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"))
    return findings


def validate_generated_syntax(fields: dict[str, list[str]], raw_text: str, rel: str) -> list[Finding]:
    if GENERATED_MARKER not in raw_text:
        return []

    findings: list[Finding] = []
    for index, value in enumerate(fields.get("A", []), 1):
        findings.extend(_validate_generated_effect(value, rel, f"A ability {index}"))

    for index, value in enumerate(fields.get("T", []), 1):
        findings.extend(_validate_generated_trigger(value, rel, f"T trigger {index}"))

    for value in fields.get("SVar", []):
        if ":" not in value:
            continue
        svar_name, body = value.split(":", 1)
        body = body.strip()
        if body.startswith(("SP$", "AB$", "DB$")):
            findings.extend(_validate_generated_effect(body, rel, f"SVar {svar_name.strip()}"))
        else:
            findings.append(
                Finding(
                    "ERROR",
                    rel,
                    f"SVar {svar_name.strip()}: unsupported generated SVar head; expected SP$, AB$, or DB$",
                )
            )

    for keyword in fields.get("K", []):
        if keyword not in SAFE_GENERATED_KEYWORDS:
            findings.append(Finding("ERROR", rel, f"generated keyword '{keyword}' is not in the grounded safe list"))

    return findings


def audit(root: Path) -> tuple[list[Finding], dict]:
    root = normalize_root(root)
    findings: list[Finding] = []
    names: dict[str, Path] = {}
    token_ids: set[str] = set()
    required_tokens: list[tuple[Path, str]] = []
    scanned = 0

    token_root = root / "tokens"
    if token_root.exists():
        for token_path in sorted(token_root.rglob("*.txt")):
            if token_path.is_file():
                token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path, root)
        findings.extend(local)
        rel = display_path(path, root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", rel, "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", rel, "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        f"duplicate Name '{name}' (also in {display_path(names[key], root)})",
                    )
                )
            else:
                names[key] = path

        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
        if REVIEW_MARKER_RE.search(raw_text) and not any(
            f.file == rel and "FORGE-NAVI REVIEW" in f.message for f in findings
        ):
            findings.append(Finding("WARN", rel, "contains TODO/FIXME or Forge-Navi review marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", rel, f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", rel, f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", rel, f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(
                Finding(
                    "ERROR",
                    display_path(source, root),
                    f"TokenScript '{token_id}' has no matching custom/tokens script",
                )
            )

    script_files = [display_path(path, root) for path in iter_script_files(root)]
    summary = {
        "root": str(root),
        "script_files": script_files,
        "scripts_scanned": scanned,
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "status": "PASS" if not any(f.severity == "ERROR" for f in findings) else "FAIL",
    }
    return findings, summary


def write_report(root: Path, out: Path) -> int:
    findings, summary = audit(root)
    payload = {"summary": summary, "findings": [asdict(f) for f in findings]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for finding in findings:
        print(f"[{finding.severity}] {finding.file}: {finding.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path, root)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append(
                {
                    "id": token_id,
                    "display_name": token_id.replace("_", " ").title(),
                    "source_card": first(fields, "Name") or path.stem,
                    "image": f"assets/{token_id}.png",
                    "status": "REQUIRED",
                }
            )
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def generate_card_script(root: Path, spec: CardSpec, overwrite: bool = False):
    root = normalize_root(root)
    draft = compile_card(spec)
    rel = script_relative_path(spec.name)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"script already exists: {path}")
    path.write_text(draft.script, encoding="utf-8")
    return path, draft


def save_workspace_zip(root: Path, out: Path) -> int:
    """Save the current working custom tree without applying the release gate."""
    root = normalize_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            zf.write(path, path.relative_to(root).as_posix())
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for finding in findings:
            if finding.severity == "ERROR":
                print(f"[ERROR] {finding.file}: {finding.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".forge-navi" not in p.relative_to(root).parts
        ):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr(
            "FORGE_NAVI_MANIFEST.json",
            json.dumps({"audit": summary, "files": manifest}, indent=2),
        )
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community QA / scripting / packaging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="Audit a Forge custom root")
    p.add_argument("root", type=Path)
    p.add_argument("--report", type=Path, default=Path("forge-navi-report.json"))

    p = sub.add_parser("handoff", help="Build token requirements from TokenScript references")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("package", help="Package only after a clean audit")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("script", help="Generate one grounded Forge script from a JSON card spec")
    p.add_argument("root", type=Path)
    p.add_argument("spec", type=Path)
    p.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    if args.cmd == "script":
        data = json.loads(args.spec.read_text(encoding="utf-8"))
        path, draft = generate_card_script(args.root, CardSpec(**data), overwrite=args.overwrite)
        print(path)
        if draft.review:
            print("REVIEW:")
            for item in draft.review:
                print(f"- {item}")
            return 1
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
