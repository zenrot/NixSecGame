import io
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from gameguard.catalog import load_catalog, load_sources
from gameguard.installer import CheckedRedirects, download_archive, safe_extract, validate_url


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = load_catalog()["beneath-a-steel-sky"]
        self.sources = load_sources()

    def test_only_catalog_source_is_allowed(self):
        validate_url(self.game["url"], self.game["source_id"], self.sources)
        for source, url in (
            ("unknown", self.game["url"]),
            (self.game["source_id"], self.game["url"].replace("https:", "http:")),
            (self.game["source_id"], self.game["url"].replace(".org/", ".org.example/")),
            (self.game["source_id"], self.game["url"].replace("/BASS-", "/%2e%2e/BASS-")),
        ):
            with self.subTest(url=url, source=source), self.assertRaises(ValueError):
                validate_url(url, source, self.sources)

    def test_redirect_uses_source_whitelist(self):
        redirects = CheckedRedirects(self.game["source_id"], self.sources)
        with self.assertRaises(ValueError):
            redirects.redirect_request(None, None, 302, "", {}, "https://example.org/game.zip")

    def test_rejects_download_with_wrong_digest(self):
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.geturl.return_value = self.game["url"]
        reply.read.side_effect = io.BytesIO(b"wrong archive").read
        with patch("gameguard.installer.build_opener") as opener:
            opener.return_value.open.return_value = reply
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                download_archive(self.game, self.sources, self.root / "wrong.zip")

    def test_extracts_plain_files(self):
        archive = self.root / "game.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("nested/sky.dsk", b"test")
        safe_extract(archive, self.root / "data")
        self.assertEqual((self.root / "data/nested/sky.dsk").read_bytes(), b"test")

    def test_rejects_unsafe_zip_paths(self):
        for path in ("../outside", "/absolute", "C:/outside", "dir\\outside"):
            with self.subTest(path=path):
                archive = self.root / "game.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr(path, b"test")
                with self.assertRaises(ValueError):
                    safe_extract(archive, self.root / "data")
        self.assertFalse((self.root / "outside").exists())

    def test_rejects_symlinks_in_zip(self):
        archive = self.root / "game.zip"
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(link, "../outside")
        with self.assertRaises(ValueError):
            safe_extract(archive, self.root / "data")
