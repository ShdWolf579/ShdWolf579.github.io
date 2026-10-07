#!/usr/bin/env python3
"""Forge-Navi Community Desktop v0.3-dev."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import forge_navi_community as core
from forge_navi_scripter import CardSpec, compile_card, script_relative_path

APP_TITLE = "Forge-Navi Community"
APP_VERSION = "0.3-dev"


def resource_path(relative: str) -> Path:
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
        self.geometry("1160x760")
        self.minsize(920, 620)

        self.source_path = tk.StringVar()
        self.status_text = tk.StringVar(value="Choose a Forge folder or ZIP, or load the included demo.")
        self.count_text = tk.StringVar(value="No audit run yet.")
        self.last_findings = []
        self.last_summary = {}
        self.active_root: Path | None = None
        self.temp_dirs: list[Path] = []
        self.loaded_zip: Path | None = None
        self.zip_dirty = False
        self.last_saved_script: Path | None = None

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Forge-Navi Community", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Audit, script, token handoff, and gated packaging for Forge custom content.",
        ).pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        self.audit_tab = ttk.Frame(notebook, padding=10)
        self.script_tab = ttk.Frame(notebook, padding=10)
        notebook.add(self.audit_tab, text="Audit & Package")
        notebook.add(self.script_tab, text="Script Card")

        self._build_audit_tab()
        self._build_script_tab()

    def _build_audit_tab(self) -> None:
        picker = ttk.Frame(self.audit_tab)
        picker.pack(fill="x", pady=(0, 10))
        ttk.Entry(picker, textvariable=self.source_path).pack(side="left", fill="x", expand=True)
        ttk.Button(picker, text="Choose Folder", command=self.choose_folder).pack(side="left", padx=(8, 0))
        ttk.Button(picker, text="Open ZIP", command=self.choose_zip).pack(side="left", padx=(8, 0))
        ttk.Button(picker, text="Load Demo", command=self.load_demo).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(self.audit_tab)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Audit", command=self.run_audit).pack(side="left")
        ttk.Button(actions, text="Open Selected Script", command=self.open_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Generate Token Handoff", command=self.generate_handoff).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Build Package", command=self.build_package).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Save ZIP As...", command=self.save_zip_as).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Working Folder", command=self.open_root).pack(side="left", padx=(8, 0))

        summary = ttk.LabelFrame(self.audit_tab, text="Audit Summary", padding=10)
        summary.pack(fill="x", pady=(0, 10))
        ttk.Label(summary, textvariable=self.count_text, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(summary, textvariable=self.status_text, wraplength=1030).pack(anchor="w", pady=(4, 0))

        results_frame = ttk.LabelFrame(self.audit_tab, text="Findings", padding=8)
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
        self.tree.column("file", width=320, anchor="w")
        self.tree.column("message", width=650, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(results_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())

        self.tree.tag_configure("RED", foreground="#b00020")
        self.tree.tag_configure("YELLOW", foreground="#9a6700")
        self.tree.tag_configure("GREEN", foreground="#137333")

        ttk.Label(
            self.audit_tab,
            text="RED = structural error • YELLOW = review recommended • GREEN = no structural findings",
        ).pack(anchor="w", pady=(8, 0))

    def _build_script_tab(self) -> None:
        form = ttk.Frame(self.script_tab)
        form.pack(fill="x")
        for col in (1, 3):
            form.columnconfigure(col, weight=1)

        self.card_name = tk.StringVar()
        self.card_mana = tk.StringVar()
        self.card_types = tk.StringVar()
        self.card_colors = tk.StringVar()
        self.card_pt = tk.StringVar()
        self.card_keywords = tk.StringVar()
        self.card_token_script = tk.StringVar()
        self.script_status = tk.StringVar(value="Enter a card and click Generate Draft.")

        fields = [
            ("Name", self.card_name, 0, 0),
            ("Mana Cost", self.card_mana, 0, 2),
            ("Types", self.card_types, 1, 0),
            ("Colors", self.card_colors, 1, 2),
            ("P/T", self.card_pt, 2, 0),
            ("Keywords", self.card_keywords, 2, 2),
            ("Token Script ID", self.card_token_script, 3, 0),
        ]
        for label, var, row, col in fields:
            ttk.Label(form, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=4)
            ttk.Entry(form, textvariable=var).grid(
                row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=4
            )

        ttk.Label(form, text="Oracle").grid(row=4, column=0, sticky="nw", padx=(0, 6), pady=(8, 4))
        self.oracle_text = tk.Text(form, height=5, wrap="word")
        self.oracle_text.grid(row=4, column=1, columnspan=3, sticky="nsew", pady=(8, 4))
        form.rowconfigure(4, weight=1)

        actions = ttk.Frame(self.script_tab)
        actions.pack(fill="x", pady=(10, 8))
        ttk.Button(actions, text="Generate Draft", command=self.generate_script_preview).pack(side="left")
        ttk.Button(actions, text="Write Script to Project", command=self.write_script).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Saved Script", command=self.open_saved_script).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Clear", command=self.clear_script_form).pack(side="left", padx=(8, 0))

        ttk.Label(self.script_tab, textvariable=self.script_status, wraplength=1040).pack(anchor="w", pady=(0, 8))

        preview_frame = ttk.LabelFrame(self.script_tab, text="Generated Forge Script", padding=8)
        preview_frame.pack(fill="both", expand=True)
        self.script_preview = tk.Text(preview_frame, wrap="none", font=("Consolas", 10))
        self.script_preview.pack(side="left", fill="both", expand=True)
        pscroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.script_preview.yview)
        pscroll.pack(side="right", fill="y")
        self.script_preview.configure(yscrollcommand=pscroll.set)

        ttk.Label(
            self.script_tab,
            text=(
                "v0.3-dev only emits grounded public patterns. Unsupported Oracle text stays YELLOW "
                "as FORGE-NAVI REVIEW instead of guessed syntax."
            ),
        ).pack(anchor="w", pady=(8, 0))

    def set_active_root(self, root: Path, source_label: str) -> None:
        self.active_root = core.normalize_root(root)
        self.source_path.set(source_label)
        self.status_text.set(f"Working root: {self.active_root}")

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choose Forge custom folder or project folder")
        if chosen:
            self.loaded_zip = None
            self.zip_dirty = False
            root = core.normalize_root(Path(chosen))
            self.set_active_root(root, chosen)
            self.run_audit()

    def choose_zip(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Open Forge custom ZIP",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        if not chosen:
            return
        temp = Path(tempfile.mkdtemp(prefix="forge-navi-zip-"))
        self.temp_dirs.append(temp)
        try:
            root = core.safe_extract_zip(Path(chosen), temp)
        except Exception as exc:
            shutil.rmtree(temp, ignore_errors=True)
            self.temp_dirs.remove(temp)
            messagebox.showerror(APP_TITLE, f"Could not open ZIP:\n{exc}")
            return
        self.loaded_zip = Path(chosen).resolve()
        self.zip_dirty = False
        self.set_active_root(root, chosen)
        self.status_text.set(
            f"ZIP loaded into a temporary working copy: {root}. "
            "Changes must be saved with Save ZIP As..."
        )
        self.run_audit()

    def load_demo(self) -> None:
        self.loaded_zip = None
        self.zip_dirty = False
        demo = resource_path("demo/custom")
        self.set_active_root(demo, "Built-in demo")
        self.run_audit()

    def current_root(self) -> Path | None:
        if self.active_root and self.active_root.exists():
            return self.active_root
        raw = self.source_path.get().strip().strip('"')
        if raw and Path(raw).is_dir():
            self.active_root = core.normalize_root(Path(raw))
            return self.active_root
        messagebox.showinfo(APP_TITLE, "Choose a Forge folder or ZIP first.")
        return None

    def clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

    def run_audit(self) -> None:
        root = self.current_root()
        if root is None:
            return
        try:
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
            self.status_text.set("No structural blockers, but yellow findings still need review.")
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
        root = self.current_root()
        if root is None:
            return None
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
        root = self.current_root()
        if root is None:
            return
        state_dir = root / ".forge-navi"
        state_dir.mkdir(parents=True, exist_ok=True)
        out = filedialog.asksaveasfilename(
            title="Save token handoff",
            initialdir=str(state_dir),
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
        root = self.current_root()
        if root is None:
            return
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
            "They do not block the structural gate. Build the package anyway?",
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

    def save_zip_as(self) -> None:
        root = self.current_root()
        if root is None:
            return
        if self.loaded_zip is None:
            messagebox.showinfo(
                APP_TITLE,
                "This project was opened as a folder. Files are already being written directly to disk."
            )
            return

        suggested = f"{self.loaded_zip.stem}-edited.zip"
        out = filedialog.asksaveasfilename(
            title="Save edited Forge ZIP",
            initialdir=str(self.loaded_zip.parent),
            initialfile=suggested,
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip")],
        )
        if not out:
            return

        try:
            core.save_workspace_zip(root, Path(out))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not save ZIP:\n{exc}")
            return

        self.loaded_zip = Path(out).resolve()
        self.zip_dirty = False
        self.source_path.set(str(self.loaded_zip))
        self.status_text.set(f"ZIP saved: {self.loaded_zip}")
        messagebox.showinfo(APP_TITLE, f"Edited ZIP saved:\n{self.loaded_zip}")

    def open_root(self) -> None:
        root = self.current_root()
        if root is None:
            return
        try:
            open_path(root)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not open folder:\n{exc}")

    def card_spec(self) -> CardSpec:
        return CardSpec(
            name=self.card_name.get(),
            mana_cost=self.card_mana.get(),
            types=self.card_types.get(),
            colors=self.card_colors.get(),
            pt=self.card_pt.get(),
            keywords=self.card_keywords.get(),
            token_script=self.card_token_script.get(),
            oracle=self.oracle_text.get("1.0", "end").strip(),
        )

    def generate_script_preview(self):
        spec = self.card_spec()
        draft = compile_card(spec)
        self.script_preview.delete("1.0", "end")
        self.script_preview.insert("1.0", draft.script)
        patterns = ", ".join(draft.proven_patterns) if draft.proven_patterns else "none"
        if draft.review:
            self.script_status.set(
                f"YELLOW draft • grounded patterns: {patterns} • "
                f"{len(draft.review)} clause(s)/field(s) need review."
            )
        else:
            self.script_status.set(f"GREEN draft • grounded patterns: {patterns}")
        return draft

    def write_script(self) -> None:
        root = self.current_root()
        if root is None:
            return
        spec = self.card_spec()
        draft = self.generate_script_preview()
        if not spec.name.strip():
            messagebox.showerror(APP_TITLE, "Card Name is required.")
            return

        rel = script_relative_path(spec.name)
        path = root / rel
        if path.exists() and not messagebox.askyesno(
            APP_TITLE, f"{rel.as_posix()} already exists.\n\nOverwrite it?"
        ):
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(draft.script, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not write script:\n{exc}")
            return

        self.last_saved_script = path
        if self.loaded_zip is not None:
            self.zip_dirty = True
            self.status_text.set(
                "ZIP working copy modified. Use Save ZIP As... before closing Navi."
            )
        self.run_audit()
        if draft.review:
            messagebox.showwarning(
                APP_TITLE,
                f"Draft written to:\n{path}\n\n"
                f"{len(draft.review)} item(s) remain YELLOW and must be reviewed.",
            )
        else:
            messagebox.showinfo(APP_TITLE, f"Script written to:\n{path}")

    def open_saved_script(self) -> None:
        if not self.last_saved_script or not self.last_saved_script.exists():
            messagebox.showinfo(APP_TITLE, "No generated script has been saved yet.")
            return
        try:
            open_path(self.last_saved_script)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not open script:\n{exc}")

    def clear_script_form(self) -> None:
        for var in (
            self.card_name,
            self.card_mana,
            self.card_types,
            self.card_colors,
            self.card_pt,
            self.card_keywords,
            self.card_token_script,
        ):
            var.set("")
        self.oracle_text.delete("1.0", "end")
        self.script_preview.delete("1.0", "end")
        self.script_status.set("Enter a card and click Generate Draft.")
        self.last_saved_script = None

    def on_close(self) -> None:
        if self.loaded_zip is not None and self.zip_dirty:
            choice = messagebox.askyesnocancel(
                APP_TITLE,
                "This ZIP has unsaved changes.\n\n"
                "Yes = Save ZIP As...\n"
                "No = discard changes and close\n"
                "Cancel = keep Navi open",
            )
            if choice is None:
                return
            if choice:
                self.save_zip_as()
                if self.zip_dirty:
                    return

        for temp in self.temp_dirs:
            shutil.rmtree(temp, ignore_errors=True)
        self.destroy()


if __name__ == "__main__":
    ForgeNaviDesktop().mainloop()
