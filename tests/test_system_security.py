"""Opt-in checks against an activated NixOS generation, as a normal user."""

import errno
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gameguard.catalog import load_catalog, manifest_for, write_json
from gameguard.static import create_baseline, trusted_baseline

HELPER = "/run/current-system/sw/bin/gameguard-network"


@unittest.skipUnless(os.environ.get("GAMEGUARD_SYSTEM_TESTS") == "1", "требуется активная NixOS-конфигурация")
class SystemSecurityTests(unittest.TestCase):
    def test_baseline_is_read_only_and_local_replacement_is_ignored(self):
        self.assertNotEqual(os.getuid(), 0)
        game_id = "beneath-a-steel-sky"
        game = load_catalog()[game_id]
        path = trusted_baseline(game)
        original = path.read_bytes()
        with self.assertRaises(OSError) as denied:
            path.write_text("{}")
        self.assertIn(denied.exception.errno, (errno.EACCES, errno.EPERM, errno.EROFS))
        self.assertEqual(path.read_bytes(), original)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "gameguard/games" / game_id
            files = destination / "files"
            files.mkdir(parents=True)
            for name in game["required_files"]:
                (files / name).write_bytes(b"tampered")
            write_json(destination / "gameguard.json", manifest_for(game_id, game))
            create_baseline(files, destination / "integrity.json")
            environment = {**os.environ, "XDG_DATA_HOME": temporary}
            verification = subprocess.run(
                ["gameguard", "verify", game_id], env=environment,
                capture_output=True, text=True,
            )
            self.assertEqual(verification.returncode, 1)
            self.assertEqual(json.loads(verification.stdout)["status"], "warning")
            launch = subprocess.run(
                ["gameguard", "run", game_id], env=environment,
                capture_output=True, text=True,
            )
            self.assertEqual(launch.returncode, 1)
            self.assertIn("отклонения", json.loads(launch.stderr)["message"])
            self.assertFalse((root / "gameguard/reports" / game_id / "network.strace").exists())

    def test_helper_cannot_grant_root_or_pass_injection_environment(self):
        identity = subprocess.run(
            ["sudo", "-n", HELPER, "{}", "/run/current-system/sw/bin/id", "-u"],
            capture_output=True, text=True,
        )
        self.assertEqual(identity.returncode, 0, identity.stderr)
        self.assertEqual(int(identity.stdout), os.getuid())
        injected = subprocess.run(
            ["sudo", "-n", HELPER, '{"LD_PRELOAD": "/tmp/evil.so"}', "/run/current-system/sw/bin/true"],
            capture_output=True, text=True,
        )
        self.assertEqual(injected.returncode, 125)
        self.assertIn("окружения", injected.stderr)
        # Running the check outside a filtered unit must fail closed.
        unfiltered = subprocess.run(
            ["sudo", "-n", HELPER, "--check"], capture_output=True, text=True,
        )
        self.assertNotEqual(unfiltered.returncode, 0)

    def test_tcp_udp_filter_allows_loopback_and_blocks_lan(self):
        interfaces = json.loads(subprocess.check_output(["ip", "-j", "address"], text=True))
        for family, loopback in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            addresses = [
                item["local"]
                for interface in interfaces
                for item in interface.get("addr_info", [])
                if item["family"] == ("inet" if family == socket.AF_INET else "inet6")
                and item["scope"] == "global"
            ]
            self.assertTrue(addresses, f"Нужен адрес интерфейса для {family.name}")
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                with self.subTest(family=family.name, protocol=kind.name), socket.socket(family, kind) as server:
                    server.bind(("0.0.0.0" if family == socket.AF_INET else "::", 0))
                    if kind == socket.SOCK_STREAM:
                        server.listen()
                    script = (
                        "import socket,sys;"
                        "s=socket.socket(int(sys.argv[1]),int(sys.argv[2]));"
                        "s.settimeout(1);"
                        "s.connect((sys.argv[3],int(sys.argv[4])));"
                        "s.send(b'probe')"
                    )
                    client = [sys.executable, "-c", script, str(family.value), str(kind.value), loopback, str(server.getsockname()[1])]
                    allowed = subprocess.run(
                        ["sudo", "-n", HELPER, "{}", *client], capture_output=True, text=True,
                    )
                    self.assertEqual(allowed.returncode, 0, allowed.stderr)
                    client[-2] = addresses[0]
                    direct = subprocess.run(client, capture_output=True, text=True)
                    self.assertEqual(direct.returncode, 0, direct.stderr)
                    blocked = subprocess.run(
                        ["sudo", "-n", HELPER, "{}", *client], capture_output=True, text=True,
                    )
                    self.assertNotEqual(blocked.returncode, 0)
