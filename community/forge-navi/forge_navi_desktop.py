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
import forge_navi_workspace as workspace
from forge_navi_scripter import CardSpec, compile_card, script_relative_path

APP_TITLE = "Forge-Navi Community"
APP_VERSION = "0.4-dev"


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
        self.workspace_summary_text = tk.StringVar(value="No set workspace loaded yet.")
        self.last_findings = []
        self.last_summary = {}
        self.inventory = None
        self.workspace_root: Path | None = None
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

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self.workspace_tab = ttk.Frame(self.notebook, padding=10)
        self.audit_tab = ttk.Frame(self.notebook, padding=10)
        self.script_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.workspace_tab, text="Set Workspace")
        self.notebook.add(self.audit_tab, text="Audit & Package")
        self.notebook.add(self.script_tab, text="Script Card")

        self._build_workspace_tab()
        self._build_audit_tab()
        self._build_script_tab()

    def _build_workspace_tab(self) -> None:
        toolbar = ttk.Frame(self.workspace_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Refresh Workspace", command=self.refresh_workspace).pack(side="left")
        ttk.Button(toolbar, text="Open Workspace Folder", command=self.open_root).pack(side="left", padx=(8, 0))
        ttk.Label(toolbar, textvariable=self.workspace_summary_text, wraplength=820).pack(
            side="left", padx=(14, 0)
        )

        frame = ttk.LabelFrame(self.workspace_tab, text="Cards", padding=8)
        frame.pack(fill="both", expand=True)

        self.workspace_tree = ttk.Treeview(
            frame,
            columns=("state", "name", "script", "art", "tokens"),
            show="headings",
            selectmode="browse",
        )
        self.workspace_tree.heading("state", text="State")
        self.workspace_tree.heading("name", text="Card")
        self.workspace_tree.heading("script", text="Script")
        self.workspace_tree.heading("art", text="Card Art")
        self.workspace_tree.heading("tokens", text="Tokens")
        self.workspace_tree.column("state", width=105, anchor="center", stretch=False)
        self.workspace_tree.column("name", width=250, anchor="w")
        self.workspace_tree.column("script", width=340, anchor="w")
        self.workspace_tree.column("art", width=120, anchor="center", stretch=False)
        self.workspace_tree.column("tokens", width=120, anchor="center", stretch=False)
        self.workspace_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.workspace_tree.yview)
        scroll.pack(side="right", fill="y")
        self.workspace_tree.configure(yscrollcommand=scroll.set)

        for state, color in (
            ("RED", "#b00020"),
            ("YELLOW", "#9a6700"),
            ("GREEN", "#137333"),
            ("ERRATA-GREEN", "#137333"),
            ("UNKNOWN", "#666666"),
        ):
            self.workspace_tree.tag_configure(state, foreground=color)

        self.workspace_tree.bind("<Double-1>", lambda _event: self.open_workspace_script())
        self.workspace_tree.bind("<Button-3>", self.show_workspace_context)

        self.workspace_menu = tk.Menu(self, tearoff=0)
        self.workspace_menu.add_command(label="Open Script", command=self.open_workspace_script)
        self.workspace_menu.add_command(label="Open Card Art", command=self.open_workspace_art)
        self.workspace_menu.add_command(label="Load into Script Card", command=self.load_workspace_card)
        self.workspace_menu.add_separator()
        self.workspace_menu.add_command(label="Fix Selected (Safe)", command=self.fix_workspace_selected)

        ttk.Label(
            self.workspace_tab,
            text=(
                "One row = one card record. Script, card art, token dependencies, and audit state "
                "are bound together in this workspace."
            ),
        ).pack(anchor="w", pady=(8, 0))

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
        self.tree.bind("<Button-3>", self.show_audit_context)

        self.tree.tag_configure("RED", foreground="#b00020")
        self.tree.tag_configure("YELLOW", foreground="#9a6700")
        self.tree.tag_configure("GREEN", foreground="#137333")
        self.tree.tag_configure("ERRATA-GREEN", foreground="#137333")

        self.audit_menu = tk.Menu(self, tearoff=0)
        self.audit_menu.add_command(label="Fix Selected (Safe)", command=self.fix_selected_safe)
        self.audit_menu.add_command(label="Open Script", command=self.open_selected)

        ttk.Label(
            self.audit_tab,
            text="RED = error • YELLOW = review • GREEN = exact clean • ERRATA-GREEN = documented clean errata",
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

    def set_workspace(self, workspace_root: Path, source_label: str) -> None:
        self.workspace_root = workspace.discover_workspace_root(workspace_root)
        self.active_root = workspace.discover_custom_root(self.workspace_root)
        self.source_path.set(source_label)
        self.status_text.set(
            f"Workspace: {self.workspace_root} • custom root: {self.active_root}"
        )

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choose Forge custom folder or project folder")
        if chosen:
            self.loaded_zip = None
            self.zip_dirty = False
            workspace_root = workspace.discover_workspace_root(Path(chosen))
            self.set_workspace(workspace_root, chosen)
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
            workspace_root, root = workspace.safe_extract_workspace_zip(Path(chosen), temp)
        except Exception as exc:
            shutil.rmtree(temp, ignore_errors=True)
            self.temp_dirs.remove(temp)
            messagebox.showerror(APP_TITLE, f"Could not open ZIP:\n{exc}")
            return
        self.loaded_zip = Path(chosen).resolve()
        self.zip_dirty = False
        self.set_workspace(workspace_root, chosen)
        self.active_root = root
        self.status_text.set(
            f"Whole-set ZIP loaded into temporary workspace: {workspace_root}. "
            "Changes must be saved with Save ZIP As..."
        )
        self.run_audit()

    def load_demo(self) -> None:
        self.loaded_zip = None
        self.zip_dirty = False
        demo = resource_path("demo")
        self.set_workspace(demo, "Built-in demo")
        self.run_audit()

    def current_root(self) -> Path | None:
        if self.active_root and self.active_root.exists():
            return self.active_root
        raw = self.source_path.get().strip().strip('"')
        if raw and Path(raw).is_dir():
            self.set_workspace(Path(raw), raw)
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

        files_with_findings = {finding.file for finding in findings}
        for file_name in summary.get("script_files", []):
            if file_name not in files_with_findings:
                clean_state = summary.get("script_statuses", {}).get(file_name, "GREEN")
                message = (
                    "Documented Forge-compatible errata; no audit findings."
                    if clean_state == "ERRATA-GREEN"
                    else "No audit findings."
                )
                self.tree.insert(
                    "", "end",
                    values=(clean_state, file_name, message),
                    tags=(clean_state,),
                )

        for finding in findings:
            state = "RED" if finding.severity == "ERROR" else "YELLOW"
            self.tree.insert("", "end", values=(state, finding.file, finding.message), tags=(state,))

        if not summary.get("script_files"):
            self.tree.insert("", "end", values=("GREEN", "—", "No scripts found."), tags=("GREEN",))

        state = "GREEN" if not summary["errors"] and not summary["warnings"] else (
            "RED" if summary["errors"] else "YELLOW"
        )
        self.count_text.set(
            f"{state} • {summary['scripts_scanned']} scripts • "
            f"{summary.get('green', 0)} GREEN • "
            f"{summary.get('errata_green', 0)} ERRATA-GREEN • "
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
        self.refresh_workspace()

    def refresh_workspace(self) -> None:
        if self.workspace_root is None or not self.workspace_root.exists():
            self.workspace_summary_text.set("No set workspace loaded yet.")
            return
        try:
            inventory = workspace.build_inventory(
                self.workspace_root,
                self.last_summary.get("script_statuses", {}),
            )
        except Exception as exc:
            self.workspace_summary_text.set(f"Workspace inventory failed: {exc}")
            return

        self.inventory = inventory
        for item in self.workspace_tree.get_children():
            self.workspace_tree.delete(item)

        for index, card in enumerate(inventory.cards):
            self.workspace_tree.insert(
                "",
                "end",
                iid=f"card-{index}",
                values=(
                    card.status,
                    card.name,
                    card.script_path,
                    card.art_state,
                    card.token_state,
                ),
                tags=(card.status,),
            )

        counts = inventory.counts
        codes = ", ".join(inventory.set_codes) if inventory.set_codes else "unknown set code"
        self.workspace_summary_text.set(
            f"{codes} • {counts['cards']} cards • "
            f"{counts['green']} GREEN • {counts['errata_green']} ERRATA-GREEN • "
            f"{counts['yellow']} YELLOW • {counts['red']} RED • "
            f"art {counts['art_present']} present / {counts['art_missing']} missing • "
            f"{counts['token_unresolved_cards']} card(s) with unresolved token deps"
        )

    def workspace_selected_card(self):
        if self.inventory is None:
            return None
        selection = self.workspace_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Select a card first.")
            return None
        iid = selection[0]
        if not iid.startswith("card-"):
            return None
        try:
            return self.inventory.cards[int(iid.split("-", 1)[1])]
        except (ValueError, IndexError):
            return None

    def show_workspace_context(self, event) -> None:
        row = self.workspace_tree.identify_row(event.y)
        if not row:
            return
        self.workspace_tree.selection_set(row)
        self.workspace_menu.tk_popup(event.x_root, event.y_root)

    def open_workspace_script(self) -> None:
        card = self.workspace_selected_card()
        if card is None or self.workspace_root is None:
            return
        path = self.workspace_root / card.script_path
        if not path.exists():
            messagebox.showerror(APP_TITLE, f"Script not found:\n{path}")
            return
        open_path(path)

    def open_workspace_art(self) -> None:
        card = self.workspace_selected_card()
        if card is None or self.workspace_root is None:
            return
        if card.art_state != "PRESENT" or not card.art_path:
            messagebox.showinfo(APP_TITLE, f"Card art state: {card.art_state}")
            return
        path = self.workspace_root / card.art_path
        if not path.exists():
            messagebox.showerror(APP_TITLE, f"Card art not found:\n{path}")
            return
        open_path(path)

    def load_workspace_card(self) -> None:
        card = self.workspace_selected_card()
        if card is None:
            return
        self.card_name.set(card.name)
        self.card_mana.set(card.mana_cost)
        self.card_types.set(card.types)
        self.card_colors.set("")
        self.card_pt.set("")
        self.card_keywords.set("")
        self.card_token_script.set(card.token_dependencies[0] if len(card.token_dependencies) == 1 else "")
        self.oracle_text.delete("1.0", "end")
        self.oracle_text.insert("1.0", card.oracle)
        self.script_preview.delete("1.0", "end")
        self.script_status.set(
            f"Loaded {card.name} from workspace. Generate Draft to compare/rebuild."
        )
        self.notebook.select(self.script_tab)

    def fix_workspace_selected(self) -> None:
        card = self.workspace_selected_card()
        if card is None or self.workspace_root is None:
            return
        path = self.workspace_root / card.script_path
        root = self.current_root()
        if root is None:
            return
        fixed, message = core.safe_repair_script(path, root)
        if fixed:
            if self.loaded_zip is not None:
                self.zip_dirty = True
            self.run_audit()
            messagebox.showinfo(APP_TITLE, message)
        else:
            messagebox.showwarning(APP_TITLE, message)

    def show_audit_context(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.audit_menu.tk_popup(event.x_root, event.y_root)

    def fix_selected_safe(self) -> None:
        selected = self.selected_finding()
        if selected is None:
            return
        path = self.resolve_finding_path(str(selected[1]))
        root = self.current_root()
        if path is None or root is None or not path.exists():
            messagebox.showerror(APP_TITLE, "Could not resolve the selected script.")
            return
        fixed, message = core.safe_repair_script(path, root)
        if fixed:
            if self.loaded_zip is not None:
                self.zip_dirty = True
            self.run_audit()
            messagebox.showinfo(APP_TITLE, message)
        else:
            messagebox.showwarning(APP_TITLE, message)

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
            whole_root = self.workspace_root or root.parent
            workspace.save_workspace_zip(whole_root, Path(out))
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
        target = self.workspace_root or root
        try:
            open_path(target)
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
