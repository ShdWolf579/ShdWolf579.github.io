#!/usr/bin/env python3
"""Forge-Navi Community Desktop v0.2."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import forge_navi_community as core

APP_TITLE = "Forge-Navi Community"
APP_VERSION = "0.2.1"


def resource_path(relative: str) -> Path:
    """Resolve bundled PyInstaller resources or normal source-tree files."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def open_path(path: Path) -> None:
    path = path.resolve()
    if sys.platform.startswith("win"):
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ForgeNaviDesktop(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1040x680")
        self.minsize(820, 540)
        self.root_path = tk.StringVar()
        self.status_text = tk.StringVar(value="Choose a Forge custom folder or load the included demo.")
        self.count_text = tk.StringVar(value="No audit run yet.")
        self.last_findings = []
        self.last_summary = {}
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Forge-Navi Community", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Launch-and-go structural QA, token handoff, and gated packaging for Forge custom content.",
        ).pack(anchor="w", pady=(0, 12))

        picker = ttk.Frame(outer)
        picker.pack(fill="x", pady=(0, 10))
        ttk.Entry(picker, textvariable=self.root_path).pack(side="left", fill="x", expand=True)
        ttk.Button(picker, text="Choose Folder", command=self.choose_folder).pack(side="left", padx=(8, 0))
        ttk.Button(picker, text="Load Demo", command=self.load_demo).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Audit", command=self.run_audit).pack(side="left")
        ttk.Button(actions, text="Open Selected Script", command=self.open_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Generate Token Handoff", command=self.generate_handoff).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Build Package", command=self.build_package).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Folder", command=self.open_root).pack(side="left", padx=(8, 0))

        summary = ttk.LabelFrame(outer, text="Audit Summary", padding=10)
        summary.pack(fill="x", pady=(0, 10))
        ttk.Label(summary, textvariable=self.count_text, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(summary, textvariable=self.status_text, wraplength=930).pack(anchor="w", pady=(4, 0))

        results_frame = ttk.LabelFrame(outer, text="Findings", padding=8)
        results_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            results_frame,
            columns=("severity", "file", "message"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("severity", text="State")
        self.tree.heading("file", text="File")
        self.tree.heading("message", text="Finding")
        self.tree.column("severity", width=80, anchor="center", stretch=False)
        self.tree.column("file", width=280, anchor="w")
        self.tree.column("message", width=580, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(results_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())

        self.tree.tag_configure("RED", foreground="#b00020")
        self.tree.tag_configure("YELLOW", foreground="#9a6700")
        self.tree.tag_configure("GREEN", foreground="#137333")

        ttk.Label(
            outer,
            text="RED = structural error • YELLOW = review recommended • GREEN = no structural findings",
        ).pack(anchor="w", pady=(8, 0))

    def current_root(self) -> Path | None:
        raw = self.root_path.get().strip().strip('"')
        if not raw:
            messagebox.showinfo(APP_TITLE, "Choose a Forge custom folder first.")
            return None
        root = Path(raw).expanduser()
        if not root.exists() or not root.is_dir():
            messagebox.showerror(APP_TITLE, f"Folder does not exist:\n{root}")
            return None
        return root

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choose Forge custom folder or project folder")
        if chosen:
            self.root_path.set(chosen)
            self.status_text.set("Ready to audit.")

    def load_demo(self) -> None:
        demo = resource_path("demo/custom")
        self.root_path.set(str(demo))
        self.run_audit()

    def clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

    def run_audit(self) -> None:
        chosen = self.current_root()
        if chosen is None:
            return
        try:
            root = core.normalize_root(chosen)
            findings, summary = core.audit(root)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Audit failed:\n{exc}")
            return

        self.last_findings = findings
        self.last_summary = summary
        self.clear_results()

        for finding in findings:
            state = "RED" if finding.severity == "ERROR" else "YELLOW"
            self.tree.insert("", "end", values=(state, finding.file, finding.message), tags=(state,))

        if not findings:
            self.tree.insert("", "end", values=("GREEN", "—", "No structural findings."), tags=("GREEN",))

        state = "GREEN" if not summary["errors"] and not summary["warnings"] else (
            "RED" if summary["errors"] else "YELLOW"
        )
        self.count_text.set(
            f"{state} • {summary['scripts_scanned']} scripts • "
            f"{summary['errors']} errors • {summary['warnings']} warnings"
        )
        if state == "GREEN":
            self.status_text.set("Structural audit is clean. Packaging gate is open.")
        elif state == "YELLOW":
            self.status_text.set("No structural blockers, but review the yellow findings before calling the set clean.")
        else:
            self.status_text.set("Packaging is blocked until the red findings are resolved.")

        state_dir = root / ".forge-navi"
        state_dir.mkdir(parents=True, exist_ok=True)
        report = state_dir / "audit-report.json"
        report.write_text(
            json.dumps(
                {"summary": summary, "findings": [core.asdict(f) for f in findings]},
                indent=2,
            ),
            encoding="utf-8",
        )

    def selected_finding(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Select a finding first.")
            return None
        values = self.tree.item(selection[0], "values")
        return values if len(values) == 3 else None

    def resolve_finding_path(self, file_value: str) -> Path | None:
        if file_value == "—":
            return None
        chosen = self.current_root()
        if chosen is None:
            return None
        root = core.normalize_root(chosen)
        candidate = Path(file_value)
        return candidate if candidate.is_absolute() else root / candidate

    def open_selected(self) -> None:
        selected = self.selected_finding()
        if selected is None:
            return
        path = self.resolve_finding_path(str(selected[1]))
        if path is None or not path.exists():
            messagebox.showerror(APP_TITLE, f"Could not resolve script:\n{selected[1]}")
            return
        try:
            open_path(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not open script:\n{exc}")

    def generate_handoff(self) -> None:
        chosen = self.current_root()
        if chosen is None:
            return
        root = core.normalize_root(chosen)
        state_dir = root / ".forge-navi"
        state_dir.mkdir(parents=True, exist_ok=True)
        out = filedialog.asksaveasfilename(
            title="Save token handoff",
            initialdir=str(root / ".forge-navi"),
            initialfile="token-requirements.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not out:
            return
        try:
            core.build_handoff(root, Path(out))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not generate handoff:\n{exc}")
            return
        messagebox.showinfo(APP_TITLE, f"Token handoff created:\n{out}")

    def build_package(self) -> None:
        chosen = self.current_root()
        if chosen is None:
            return
        root = core.normalize_root(chosen)
        findings, summary = core.audit(root)

        if summary["errors"]:
            messagebox.showerror(
                APP_TITLE,
                f"Packaging blocked.\n\n{summary['errors']} structural error(s) remain. "
                "Run Audit and fix the RED findings first.",
            )
            self.run_audit()
            return

        if summary["warnings"] and not messagebox.askyesno(
            APP_TITLE,
            f"There are {summary['warnings']} YELLOW finding(s).\n\n"
            "They do not block the public structural gate. Build the package anyway?",
        ):
            return

        out = filedialog.asksaveasfilename(
            title="Build Forge package",
            initialfile=f"{root.name or 'forge-custom'}-forge-navi.zip",
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip")],
        )
        if not out:
            return

        try:
            rc = core.package(root, Path(out))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Packaging failed:\n{exc}")
            return

        if rc == 0:
            messagebox.showinfo(APP_TITLE, f"Package created:\n{out}")
        else:
            messagebox.showerror(APP_TITLE, "Packaging was blocked by the audit.")

    def open_root(self) -> None:
        chosen = self.current_root()
        if chosen is None:
            return
        try:
            open_path(core.normalize_root(chosen))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not open folder:\n{exc}")


if __name__ == "__main__":
    ForgeNaviDesktop().mainloop()
