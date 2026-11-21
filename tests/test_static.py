import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gameguard.__main__ import launch
from gameguard.static import create_baseline, make_sbom, scan_integrity


class StaticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = self.root / "files"
        self.files.mkdir()
        (self.files / "sky.dnr").write_bytes(b"original")
        (self.files / "sky.dsk").write_bytes(b"disk")
        self.baseline = self.root / "integrity.json"

    def test_verify_and_block_launch(self):
        create_baseline(self.files, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        self.assertEqual(baseline["files"]["sky.dnr"], hashlib.sha256(b"original").hexdigest())
        self.assertEqual(scan_integrity(self.files, self.baseline)["status"], "ok")

        (self.files / "sky.dnr").write_bytes(b"tampered")
        (self.files / "sky.dsk").unlink()
        (self.files / "extra").symlink_to("nonexistent")
        result = scan_integrity(self.files, self.baseline)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["added"], ["extra"])
        self.assertEqual(result["removed"], ["sky.dsk"])
        self.assertEqual(result["changed"], ["sky.dnr"])

        with patch("gameguard.__main__.subprocess.run") as start, patch(
            "gameguard.__main__.trusted_baseline", return_value=self.baseline,
        ):
            with self.assertRaisesRegex(ValueError, "GameGuard обнаружил отклонения"):
                launch({}, self.files, self.root, self.root / "reports")
            start.assert_not_called()

    def test_old_install_without_baseline_is_blocked(self):
        self.assertEqual(scan_integrity(self.files, self.baseline)["status"], "error")

    def test_sbom_validates_resources_and_libraries(self):
        engine = self.root / ".scummvm-wrapped"
        engine.write_bytes(b"\x7fELF example")
        runner = self.root / "scummvm"
        runner.write_text("#!/bin/sh\n")
        library = self.root / "libengine.so"
        library.write_bytes(b"\x7fELF library")
        output = self.root / "test.cdx.json"
        with patch("gameguard.static.subprocess.run") as lddtree:
            lddtree.return_value.returncode = 0
            lddtree.return_value.stderr = ""
            lddtree.return_value.stdout = f"{engine}\n{library}\n"
            result = make_sbom(self.files, runner, output)
        self.assertTrue(result["schema_valid"])
        self.assertEqual(result["elf_dependencies"], 1)
        self.assertEqual(result["game_resources"], 2)
        bom = json.loads(output.read_text())
        self.assertEqual(
            [(entry["name"], entry["type"]) for entry in bom["components"]],
            [("libengine.so", "library"), ("sky.dnr", "file"), ("sky.dsk", "file")],
        )
