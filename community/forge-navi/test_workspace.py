import tempfile
import unittest
import zipfile
from pathlib import Path

import forge_navi_workspace as ws


class WorkspaceTests(unittest.TestCase):
    def make_workspace(self, root: Path):
        custom = root / "custom"
        card = custom / "cards" / "b" / "bright_study.txt"
        token = custom / "tokens" / "demo_token.txt"
        art = root / "pics" / "cards" / "TST" / "Bright Study.full.jpg"
        card.parent.mkdir(parents=True)
        token.parent.mkdir(parents=True)
        art.parent.mkdir(parents=True)
        card.write_text(
            "Name:Bright Study\n"
            "ManaCost:1 W U\n"
            "Types:Sorcery\n"
            "A:SP$ Token | TokenScript$ demo_token | TokenAmount$ 1\n"
            "Oracle:Create a token.\n",
            encoding="utf-8",
        )
        token.write_text("Name:Demo Token\nTypes:Creature Scout\nPT:1/1\n", encoding="utf-8")
        art.write_bytes(b"fake-jpg-for-inventory")
        return custom, card, token, art

    def test_workspace_root_from_custom_or_wrapper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Set"
            custom, *_ = self.make_workspace(root)
            self.assertEqual(ws.discover_workspace_root(root), root.resolve())
            self.assertEqual(ws.discover_workspace_root(custom), root.resolve())
            self.assertEqual(ws.discover_custom_root(root), custom.resolve())

    def test_inventory_binds_script_art_and_token(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Set"
            self.make_workspace(root)
            inv = ws.build_inventory(root, {"cards/b/bright_study.txt": "GREEN"})
            self.assertEqual(inv.counts["cards"], 1)
            self.assertEqual(inv.counts["green"], 1)
            self.assertEqual(inv.counts["art_present"], 1)
            self.assertEqual(inv.counts["token_unresolved_cards"], 0)
            self.assertEqual(inv.set_codes, ["TST"])
            card = inv.cards[0]
            self.assertEqual(card.name, "Bright Study")
            self.assertEqual(card.art_state, "PRESENT")
            self.assertEqual(card.token_state, "RESOLVED")

    def test_inventory_reports_missing_art_and_token(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Set"
            custom = root / "custom"
            card = custom / "cards" / "l" / "lonely.txt"
            card.parent.mkdir(parents=True)
            card.write_text(
                "Name:Lonely\nTypes:Sorcery\n"
                "A:SP$ Token | TokenScript$ absent_token | TokenAmount$ 1\n",
                encoding="utf-8",
            )
            inv = ws.build_inventory(root, {"cards/l/lonely.txt": "YELLOW"})
            self.assertEqual(inv.counts["art_missing"], 1)
            self.assertEqual(inv.counts["token_unresolved_cards"], 1)
            self.assertEqual(inv.cards[0].token_state, "MISSING 1")

    def test_bare_custom_zip_stays_inside_extraction_root(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = td / "bare-custom.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr(
                    "cards/b/broken.txt",
                    "Name:Broken\nTypes:Sorcery\nOracle:Draw a card.\n",
                )
                zf.writestr(
                    "tokens/demo.txt",
                    "Name:Demo\nTypes:Creature Scout\nPT:1/1\n",
                )
            extraction = td / "extract"
            workspace_root, custom_root = ws.safe_extract_workspace_zip(zpath, extraction)
            self.assertEqual(workspace_root, extraction.resolve())
            self.assertEqual(custom_root, extraction.resolve())
            self.assertTrue((custom_root / "cards/b/broken.txt").exists())

    def test_workspace_snapshot_detects_external_edit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Set"
            _custom, card, _token, _art = self.make_workspace(root)
            before = ws.workspace_snapshot(root)
            card.write_text(
                "Name:Bright Study\nTypes:Sorcery\nA:SP$ TotallyFake\n",
                encoding="utf-8",
            )
            after = ws.workspace_snapshot(root)
            self.assertNotEqual(before, after)
            self.assertNotEqual(
                before["custom/cards/b/bright_study.txt"],
                after["custom/cards/b/bright_study.txt"],
            )

    def test_full_workspace_zip_round_trip_preserves_pics(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            root = td / "Set"
            self.make_workspace(root)
            out = td / "edited.zip"
            count = ws.save_workspace_zip(root, out)
            self.assertGreaterEqual(count, 3)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
            self.assertIn("custom/cards/b/bright_study.txt", names)
            self.assertIn("custom/tokens/demo_token.txt", names)
            self.assertIn("pics/cards/TST/Bright Study.full.jpg", names)

            extracted = td / "extracted"
            workspace_root, custom_root = ws.safe_extract_workspace_zip(out, extracted)
            self.assertEqual(custom_root, workspace_root / "custom")
            self.assertTrue((workspace_root / "pics/cards/TST/Bright Study.full.jpg").exists())

    def test_forge_image_stem_matches_expected_removals(self):
        self.assertEqual(ws.forge_image_stem('Basic Spell: "Fireball"?'), "Basic Spell Fireball")


if __name__ == "__main__":
    unittest.main()
