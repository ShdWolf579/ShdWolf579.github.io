import tempfile
import unittest
import zipfile
from pathlib import Path

import forge_navi_community as core
from forge_navi_scripter import CardSpec, compile_card


class CommunityCoreTests(unittest.TestCase):
    def test_nested_custom_root_and_clean_paths(self):
        with tempfile.TemporaryDirectory() as td:
            wrapper = Path(td) / "Outer" / "Project"
            root = wrapper / "custom"
            card = root / "cards" / "b" / "broken.txt"
            card.parent.mkdir(parents=True)
            card.write_text("ManaCost:1 R\n", encoding="utf-8")
            edition = wrapper / "editions" / "Set.txt"
            edition.parent.mkdir(parents=True)
            edition.write_text("Not:A card\n", encoding="utf-8")

            self.assertEqual(core.normalize_root(Path(td)), root.resolve())
            findings, summary = core.audit(Path(td))
            self.assertEqual(summary["scripts_scanned"], 1)
            self.assertTrue(any(f.file == "cards/b/broken.txt" for f in findings))
            self.assertTrue(any("missing Name" in f.message for f in findings))

    def test_zip_input(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = td / "set.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr(
                    "Wrapper/custom/cards/d/demo.txt",
                    "Name:Demo\nTypes:Sorcery\nA:SP$ Draw | NumCards$ 1\nOracle:Draw a card.\n",
                )
            out = td / "extract"
            root = core.safe_extract_zip(zpath, out)
            self.assertEqual(root.name, "custom")
            findings, summary = core.audit(root)
            self.assertEqual(summary["scripts_scanned"], 1)
            self.assertEqual(summary["errors"], 0)

    def test_audit_reports_clean_script_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "custom"
            card = root / "cards" / "g" / "good.txt"
            card.parent.mkdir(parents=True)
            card.write_text("Name:Good\nTypes:Sorcery\nOracle:Draw a card.\n", encoding="utf-8")
            findings, summary = core.audit(root)
            self.assertEqual(findings, [])
            self.assertEqual(summary["script_files"], ["cards/g/good.txt"])

    def test_save_workspace_zip(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            root = td / "custom"
            card = root / "cards" / "s" / "saved.txt"
            card.parent.mkdir(parents=True)
            card.write_text("Name:Saved\nTypes:Sorcery\nOracle:Draw a card.\n", encoding="utf-8")
            state = root / ".forge-navi" / "audit-report.json"
            state.parent.mkdir(parents=True)
            state.write_text("{}", encoding="utf-8")
            out = td / "edited.zip"
            self.assertEqual(core.save_workspace_zip(root, out), 0)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
            self.assertIn("cards/s/saved.txt", names)
            self.assertFalse(any(name.startswith(".forge-navi/") for name in names))

    def test_generator_simple_spell_chain(self):
        draft = compile_card(
            CardSpec(
                name="Bright Study",
                mana_cost="1 W U",
                types="Sorcery",
                oracle="Draw two cards. You gain 3 life.",
            )
        )
        self.assertEqual(draft.review, [])
        self.assertIn("# FORGE-NAVI GENERATED", draft.script)
        self.assertIn("A:SP$ Draw", draft.script)
        self.assertIn("SubAbility$ DBEffect2", draft.script)
        self.assertIn("SVar:DBEffect2:DB$ GainLife", draft.script)
        self.assertIn("DRAW_EFFECT", draft.proven_patterns)
        self.assertIn("GAIN_LIFE_EFFECT", draft.proven_patterns)

    def test_generator_phase_trigger(self):
        draft = compile_card(
            CardSpec(
                name="Patient Sage",
                mana_cost="2 U",
                types="Creature Human Wizard",
                pt="2/3",
                oracle="At the beginning of your upkeep, draw a card.",
            )
        )
        self.assertEqual(draft.review, [])
        self.assertIn("T:Mode$ Phase | Phase$ Upkeep", draft.script)
        self.assertIn("SVar:TrigEffect1:DB$ Draw | NumCards$ 1", draft.script)

    def test_generated_script_uses_real_newlines(self):
        draft = compile_card(
            CardSpec(
                name="Line Test",
                mana_cost="1 U",
                types="Sorcery",
                oracle="Draw a card.",
            )
        )
        self.assertIn("Name:Line Test\nManaCost:1 U\nTypes:Sorcery\n", draft.script)
        self.assertNotIn("\\n", draft.script)

    def test_generated_bad_api_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "custom"
            card = root / "cards" / "b" / "bad_generated.txt"
            card.parent.mkdir(parents=True)
            card.write_text(
                "# FORGE-NAVI GENERATED\n"
                "Name:Bad Generated\n"
                "Types:Sorcery\n"
                "A:SP$ TotallyFake | NumCards$ 1\n"
                "Oracle:Draw a card.\n",
                encoding="utf-8",
            )
            findings, summary = core.audit(root)
            self.assertEqual(summary["red_scripts"], 1)
            self.assertTrue(any("unknown generated API 'TotallyFake'" in f.message for f in findings))

    def test_generated_missing_required_param_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "custom"
            card = root / "cards" / "b" / "bad_draw.txt"
            card.parent.mkdir(parents=True)
            card.write_text(
                "# FORGE-NAVI GENERATED\n"
                "Name:Bad Draw\n"
                "Types:Sorcery\n"
                "A:SP$ Draw\n"
                "Oracle:Draw a card.\n",
                encoding="utf-8",
            )
            findings, summary = core.audit(root)
            self.assertEqual(summary["red_scripts"], 1)
            self.assertTrue(any("missing required 'NumCards$'" in f.message for f in findings))

    def test_green_and_errata_green_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "custom"
            green = root / "cards" / "g" / "green.txt"
            errata = root / "cards" / "e" / "errata.txt"
            green.parent.mkdir(parents=True)
            errata.parent.mkdir(parents=True)
            green.write_text("Name:Green\nTypes:Sorcery\nOracle:Draw a card.\n", encoding="utf-8")
            errata.write_text(
                "# FORGE-NAVI STATUS: ERRATA-GREEN\n"
                "Name:Errata\nTypes:Sorcery\nOracle:Draw a card.\n",
                encoding="utf-8",
            )
            findings, summary = core.audit(root)
            self.assertEqual(findings, [])
            self.assertEqual(summary["green"], 1)
            self.assertEqual(summary["errata_green"], 1)
            self.assertEqual(summary["script_statuses"]["cards/e/errata.txt"], "ERRATA-GREEN")

    def test_unknown_oracle_stays_review(self):
        draft = compile_card(
            CardSpec(
                name="Weird Thing",
                mana_cost="1 U",
                types="Sorcery",
                oracle="Exchange the moon with your library.",
            )
        )
        self.assertTrue(draft.review)
        self.assertIn("FORGE-NAVI REVIEW", draft.script)


if __name__ == "__main__":
    unittest.main()
