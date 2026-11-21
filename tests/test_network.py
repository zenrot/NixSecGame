import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gameguard.network import check_filter, service_command


class NetworkSecurityTests(unittest.TestCase):
    def test_rejects_root_identity_and_privileged_environment(self):
        with self.assertRaises(ValueError):
            service_command(0, {}, ["/bin/true"])
        account = SimpleNamespace(pw_gid=100, pw_dir="/home/user", pw_name="user")
        with patch("gameguard.network.pwd.getpwuid", return_value=account):
            for variable in ("LD_PRELOAD", "PYTHONPATH", "SUDO_UID", "PATH"):
                with self.subTest(variable=variable), self.assertRaises(ValueError):
                    service_command(1000, {variable: "malicious"}, ["/bin/true"])

    def test_missing_bpf_filter_blocks_execution(self):
        reply = SimpleNamespace(stdout=json.dumps([
            {"attach_type": "cgroup_inet_ingress"},
        ]))
        with patch("gameguard.network.Path.read_text", return_value="0::/system.slice/gameguard-net-test.service"), patch(
            "gameguard.network.subprocess.run", return_value=reply,
        ):
            with self.assertRaisesRegex(RuntimeError, "IP-фильтр"):
                check_filter()
