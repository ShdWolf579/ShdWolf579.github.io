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
