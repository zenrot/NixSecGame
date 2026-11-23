import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gameguard.__main__ import handle, install, uninstall
from gameguard.catalog import load_catalog, manifest_for, read_json, write_json
from gameguard.static import create_baseline


class UninstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data/gameguard"
        self.catalog = load_catalog()
        self.game_id = "beneath-a-steel-sky"
        self.game = self.catalog[self.game_id]
        self.destination = self.root / "games" / self.game_id
        self.reports = self.root / "reports" / self.game_id
        self.files = self.destination / "files"
        self.files.mkdir(parents=True)
        for name in self.game["required_files"]:
            (self.files / name).write_bytes(b"original")
        write_json(self.destination / "gameguard.json", manifest_for(self.game_id, self.game))
        environment = patch.dict("os.environ", {"XDG_CONFIG_HOME": str(Path(temporary.name) / "config")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_removes_resources_and_pegasus_entry_preserving_saves_and_reports(self):
        saves = self.destination / "saves"
        saves.mkdir()
        (saves / "slot.sav").write_bytes(b"saved game")
        write_json(self.reports / "run.json", {"status": "ok", "exit_code": 0})
        other = "flight-of-the-amazon-queen"
        write_json(self.root / "games" / other / "gameguard.json", manifest_for(other, self.catalog[other]))

        result = uninstall(self.root, self.catalog, self.game_id, self.reports)

        self.assertTrue(result["removed"])
        self.assertEqual(result["preserved_saves"], str(saves))
        self.assertFalse(self.files.exists())
        self.assertFalse((self.destination / "gameguard.json").exists())
        self.assertEqual((saves / "slot.sav").read_bytes(), b"saved game")
        self.assertEqual(read_json(self.reports / "run.json")["exit_code"], 0)
        metadata = Path(result["pegasus"]).read_text()
        self.assertNotIn(f"launch: gameguard run {self.game_id}", metadata)
        self.assertIn(f"launch: gameguard run {other}", metadata)
        report, code = handle(argparse.Namespace(action="report", game=self.game_id), self.catalog, self.root)
        self.assertEqual(code, 0)
        self.assertFalse(report["installed"])
        self.assertEqual(report["uninstall"]["status"], "ok")

    def test_corrupt_manifest_and_repeat_uninstall_are_supported(self):
        (self.destination / "gameguard.json").write_text("broken JSON")
        self.assertTrue(uninstall(self.root, self.catalog, self.game_id, self.reports)["removed"])
        self.assertFalse(self.destination.exists())
        self.assertFalse(uninstall(self.root, self.catalog, self.game_id, self.reports)["removed"])

    def test_reinstall_keeps_save_files_in_place(self):
        baseline = self.root / "baseline.json"
        create_baseline(self.files, baseline)
        saves = self.destination / "saves"
        saves.mkdir()
        save = saves / "slot.sav"
        save.write_bytes(b"saved game")
        inode = save.stat().st_ino
        uninstall(self.root, self.catalog, self.game_id, self.reports)

        def extract(game, sources, stage):
            (stage / "files").mkdir()
            for name in game["required_files"]:
                (stage / "files" / name).write_bytes(b"original")

        with patch("gameguard.__main__.backend", return_value=None), patch(
            "gameguard.__main__.install_files", side_effect=extract,
        ), patch("gameguard.__main__.trusted_baseline", return_value=baseline):
            result = install(self.root, self.catalog, self.game_id, self.game, self.reports)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(save.read_bytes(), b"saved game")
        self.assertEqual(save.stat().st_ino, inode)
        self.assertTrue((self.destination / "gameguard.json").is_file())
        self.assertIn(f"launch: gameguard run {self.game_id}", Path(result["pegasus"]).read_text())

    def test_symbolic_links_cannot_delete_external_files(self):
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep"
        marker.write_bytes(b"keep")
        (self.destination / "external").symlink_to(outside, target_is_directory=True)
        uninstall(self.root, self.catalog, self.game_id, self.reports)
        self.assertEqual(marker.read_bytes(), b"keep")

        self.destination.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "символической"):
            uninstall(self.root, self.catalog, self.game_id, self.reports)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_failed_reinstall_does_not_move_or_delete_saves(self):
        baseline = self.root / "baseline.json"
        create_baseline(self.files, baseline)
        saves = self.destination / "saves"
        saves.mkdir()
        save = saves / "slot.sav"
        save.write_bytes(b"saved game")
        uninstall(self.root, self.catalog, self.game_id, self.reports)
        original_rename = Path.rename

        def extract(game, sources, stage):
            (stage / "files").mkdir()
            for name in game["required_files"]:
                (stage / "files" / name).write_bytes(b"original")

        def rename(source, target):
            if source.name == "gameguard.json":
                raise OSError("Cannot publish manifest")
            return original_rename(source, target)

        with patch("gameguard.__main__.backend", return_value=None), patch(
            "gameguard.__main__.install_files", side_effect=extract,
        ), patch("gameguard.__main__.trusted_baseline", return_value=baseline), patch.object(
            Path, "rename", rename,
        ), self.assertRaisesRegex(OSError, "Cannot publish"):
            install(self.root, self.catalog, self.game_id, self.game, self.reports)

        self.assertEqual(save.read_bytes(), b"saved game")
        self.assertFalse(self.files.exists())
        self.assertFalse((self.destination / "gameguard.json").exists())

    def test_parent_links_and_path_traversal_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "идентификатор"):
            uninstall(self.root, self.catalog, "../outside", self.reports)
        outside = self.root / "outside"
        outside.mkdir()
        games = self.root / "games"
        games.rename(self.root / "original-games")
        games.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "символической"):
            uninstall(self.root, self.catalog, self.game_id, self.reports)
