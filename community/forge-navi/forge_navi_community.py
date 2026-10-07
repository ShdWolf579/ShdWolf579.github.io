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

SVAR_REF_RE = re.compile(r"(?:Execute\\$|SubAbility\\$|References\\$)\\s*([A-Za-z0-9_]+)")
TOKEN_SCRIPT_RE = re.compile(r"TokenScript\\$\\s*([A-Za-z0-9_.-]+)")
REVIEW_MARKER_RE = re.compile(r"(?:TODO|FIXME|FORGE-NAVI REVIEW)", re.I)


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

    summary = {
        "root": str(root),
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
