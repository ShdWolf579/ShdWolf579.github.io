#!/usr/bin/env python3
"""Token-Navi Community Demo.

Validates Forge-Navi token handoffs and binds approvals to exact PNG bytes using SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        header = f.read(24)
    if len(header) < 24 or header[:8] != PNG_SIG or header[12:16] != b"IHDR":
        raise ValueError("not a valid PNG header")
    return struct.unpack(">II", header[16:24])


def load_handoff(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError("tokens must be a list")
    ids = set()
    for i, token in enumerate(tokens):
        if not isinstance(token, dict):
            raise ValueError(f"tokens[{i}] must be an object")
        token_id = token.get("id")
        image = token.get("image")
        if not token_id or not image:
            raise ValueError(f"tokens[{i}] requires id and image")
        if token_id.casefold() in ids:
            raise ValueError(f"duplicate token id: {token_id}")
        ids.add(token_id.casefold())
    return data


def validate(path: Path) -> int:
    data = load_handoff(path)
    print(f"PASS: {len(data['tokens'])} token requirement(s) are structurally valid.")
    return 0


def approve(handoff: Path, token_id: str, png: Path, out: Path) -> int:
    data = load_handoff(handoff)
    token = next((t for t in data["tokens"] if t["id"].casefold() == token_id.casefold()), None)
    if token is None:
        print(f"Unknown token id: {token_id}", file=sys.stderr)
        return 2
    width, height = png_size(png)
    approval = {
        "schema_version": 1,
        "token_id": token["id"],
        "source_handoff": handoff.name,
        "file": png.name,
        "sha256": sha256(png),
        "width": width,
        "height": height,
        "approved": True,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(approval, indent=2), encoding="utf-8")
    print(f"Approved {token['id']} -> {out}")
    return 0


def verify(approval_path: Path, png: Path) -> int:
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    actual = sha256(png)
    expected = approval.get("sha256")
    if actual != expected:
        print("FAIL: PNG hash does not match approval.", file=sys.stderr)
        return 2
    width, height = png_size(png)
    if width != approval.get("width") or height != approval.get("height"):
        print("FAIL: PNG dimensions do not match approval.", file=sys.stderr)
        return 2
    print(f"PASS: {approval.get('token_id')} matches the approved PNG exactly.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Token-Navi Community handoff / exact-PNG approval demo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="Validate a token requirements handoff")
    p.add_argument("handoff", type=Path)

    p = sub.add_parser("approve", help="Bind approval to exact PNG bytes")
    p.add_argument("handoff", type=Path)
    p.add_argument("token_id")
    p.add_argument("png", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("verify", help="Verify a PNG against an approval record")
    p.add_argument("approval", type=Path)
    p.add_argument("png", type=Path)

    args = parser.parse_args()
    try:
        if args.cmd == "validate":
            return validate(args.handoff)
        if args.cmd == "approve":
            return approve(args.handoff, args.token_id, args.png, args.out)
        if args.cmd == "verify":
            return verify(args.approval, args.png)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
