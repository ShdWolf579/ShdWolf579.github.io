#!/usr/bin/env python3
"""Forge-Navi Community Core v0.2.

A small, dependency-free structural QA and packaging tool for Forge custom content.
It intentionally does not include the private Forge-Navi semantic knowledge corpus.
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

SVAR_REF_RE = re.compile(r"(?:Execute\$|SubAbility\$|References\$)\s*([A-Za-z0-9_]+)")
TOKEN_SCRIPT_RE = re.compile(r"TokenScript\$\s*([A-Za-z0-9_.-]+)")


def normalize_root(root: Path) -> Path:
    """Accept either a Forge custom folder or a project folder containing custom/."""
    root = root.expanduser().resolve()
    nested = root / "custom"
    if nested.is_dir() and ((nested / "cards").exists() or (nested / "tokens").exists()):
        return nested
    return root


@dataclass
class Finding:
    severity: str
    file: str
    message: str


def iter_script_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.txt")):
        if path.is_file():
            yield path


def parse_script(path: Path) -> tuple[dict[str, list[str]], list[Finding]]:
    fields: dict[str, list[str]] = {}
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            findings.append(Finding("WARN", str(path), f"line {lineno}: no ':' separator"))
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
        for token_path in iter_script_files(token_root):
            token_ids.add(token_path.stem.lower())

    for path in iter_script_files(root):
        scanned += 1
        fields, local = parse_script(path)
        findings.extend(local)
        rel = path.relative_to(root)

        name = first(fields, "Name")
        types = first(fields, "Types")
        if not name:
            findings.append(Finding("ERROR", str(rel), "missing Name field"))
        if not types:
            findings.append(Finding("ERROR", str(rel), "missing Types field"))

        if name:
            key = name.casefold()
            if key in names:
                findings.append(Finding("ERROR", str(rel), f"duplicate Name '{name}' (also in {names[key].relative_to(root)})"))
            else:
                names[key] = path

        if any("TODO" in value.upper() or "FIXME" in value.upper() for values in fields.values() for value in values):
            findings.append(Finding("WARN", str(rel), "contains TODO/FIXME marker"))

        svars: dict[str, str] = {}
        for value in fields.get("SVar", []):
            if ":" not in value:
                findings.append(Finding("ERROR", str(rel), f"malformed SVar '{value}'"))
                continue
            svar_name, body = value.split(":", 1)
            svar_name = svar_name.strip()
            if svar_name in svars:
                findings.append(Finding("ERROR", str(rel), f"duplicate SVar '{svar_name}'"))
            svars[svar_name] = body

        all_values = "\n".join(v for values in fields.values() for v in values)
        for ref in SVAR_REF_RE.findall(all_values):
            if ref not in svars and ref not in {"DBEffect", "None"}:
                findings.append(Finding("WARN", str(rel), f"referenced SVar '{ref}' is not defined in this file"))

        for token_id in TOKEN_SCRIPT_RE.findall(all_values):
            required_tokens.append((path, token_id))

    for source, token_id in required_tokens:
        if token_id.lower() not in token_ids:
            findings.append(Finding("ERROR", str(source.relative_to(root)), f"TokenScript '{token_id}' has no matching custom/tokens script"))

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
    for f in findings:
        print(f"[{f.severity}] {f.file}: {f.message}")
    return 0 if summary["status"] == "PASS" else 2


def build_handoff(root: Path, out: Path) -> int:
    root = normalize_root(root)
    requirements = []
    seen = set()
    for path in iter_script_files(root):
        fields, _ = parse_script(path)
        text = "\n".join(v for values in fields.values() for v in values)
        for token_id in TOKEN_SCRIPT_RE.findall(text):
            if token_id.casefold() in seen:
                continue
            seen.add(token_id.casefold())
            requirements.append({
                "id": token_id,
                "display_name": token_id.replace("_", " ").title(),
                "source_card": first(fields, "Name") or path.stem,
                "image": f"assets/{token_id}.png",
                "status": "REQUIRED",
            })
    payload = {"schema_version": 1, "generator": "Forge-Navi Community", "tokens": requirements}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(requirements)} token requirement(s) to {out}")
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package(root: Path, out: Path) -> int:
    root = normalize_root(root)
    findings, summary = audit(root)
    if summary["status"] != "PASS":
        print("Packaging blocked: audit has errors.", file=sys.stderr)
        for f in findings:
            if f.severity == "ERROR":
                print(f"[ERROR] {f.file}: {f.message}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in root.rglob("*") if p.is_file() and ".forge-navi" not in p.relative_to(root).parts):
            rel = path.relative_to(root).as_posix()
            zf.write(path, rel)
            manifest.append({"path": rel, "sha256": sha256(path)})
        zf.writestr("FORGE_NAVI_MANIFEST.json", json.dumps({"audit": summary, "files": manifest}, indent=2))
    print(f"Created {out} with {len(manifest)} file(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge-Navi Community structural QA / packaging demo")
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

    args = parser.parse_args()
    if args.cmd == "audit":
        return write_report(args.root, args.report)
    if args.cmd == "handoff":
        return build_handoff(args.root, args.out)
    if args.cmd == "package":
        return package(args.root, args.out)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
