import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from gameguard import dynamic

CLIENT = Path(__file__).with_name("tcp_client.py")

SAMPLE_NETWORK = """\
101   1700000000.100000 socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = 3
101   1700000000.100100 connect(3, {sa_family=AF_INET, sin_port=htons(5000), sin_addr=inet_addr("127.0.0.1")}, 16) = 0
101   1700000000.100200 connect(4, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("93.184.216.34")}, 16) = -1 EINPROGRESS (Operation now in progress)
101   1700000000.100300 connect(5, {sa_family=AF_INET6, sin6_port=htons(80), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "::1", &sin6_addr), sin6_scope_id=0}, 28) = -1 ECONNREFUSED (Connection refused)
101   1700000000.100400 connect(6, {sa_family=AF_UNIX, sun_path="/tmp/.X11-unix/X0"}, 110) = 0
101   1700000000.100250 getsockopt(4, SOL_SOCKET, SO_ERROR, [0], [4]) = 0
101   1700000000.100350 connect(8, {sa_family=AF_INET, sin_port=htons(8080), sin_addr=inet_addr("127.0.0.1")}, 16) = -1 EINPROGRESS (Operation now in progress)
101   1700000000.100360 getsockopt(8, SOL_SOCKET, SO_ERROR, [ECONNREFUSED], [4]) = 0
102   1700000000.100500 connect(7, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("8.8.8.8")}, 16 <unfinished ...>
102   1700000000.100600 <... connect resumed>) = 0
"""

SAMPLE_INSTALL = """\
201   1700000000.000000 execve("/usr/bin/python3", ["python3", "-m", "gameguard"], 0x7ffd /* 20 vars */) = 0
201   1700000000.000100 openat(AT_FDCWD, "/etc/missing.conf", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)
201   1700000000.000200 openat(AT_FDCWD, "/stage/files/sky.dsk", O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC, 0666) = 3
201   1700000000.000300 openat(AT_FDCWD, "/home/user/.bashrc", O_WRONLY|O_APPEND) = 4
201   1700000000.000400 openat(AT_FDCWD, "/root/secret", O_RDONLY) = -1 EACCES (Permission denied)
201   1700000000.000500 clone(child_stack=NULL, flags=CLONE_VM) = 202
202   1700000000.000600 execve("/bin/true", ["true"], 0x7ffd /* 20 vars */) = 0
202   1700000000.000700 +++ exited with 3 +++
201   1700000000.000800 rename("/stage/game.zip", "/stage/files/game.zip") = 0
201   1700000000.000900 unlink("/stage/tmp") = 0
201   1700000000.001000 +++ exited with 0 +++
"""


def strace_works():
    if shutil.which("strace") is None:
        return False
    return subprocess.run(["strace", "-o", "/dev/null", "true"], capture_output=True).returncode == 0


class TcpServer:
    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        connection, _ = self.socket.accept()
        with connection:
            connection.recv(16)
            connection.sendall(b"pong")

    def close(self):
        self.thread.join(timeout=5)
        self.socket.close()


class ParserTests(unittest.TestCase):
    def test_network_summary_checks_policy(self):
        calls, _ = dynamic.parse_trace(SAMPLE_NETWORK)
        networks = dynamic.load_policy()
        summary = dynamic.summarize_network(calls, networks)
        by_ip = {item["ip"]: item for item in summary["connections"] if item["port"] != 8080}

        self.assertEqual(by_ip["127.0.0.1"]["port"], 5000)
        self.assertEqual(by_ip["127.0.0.1"]["result"], "success")
        self.assertTrue(by_ip["127.0.0.1"]["allowed"])
        self.assertEqual(by_ip["93.184.216.34"]["result"], "success", "SO_ERROR=0 завершает connect")
        self.assertFalse(by_ip["93.184.216.34"]["allowed"])
        self.assertEqual(by_ip["::1"]["errno"], "ECONNREFUSED")
        refused = [c for c in summary["connections"] if c["port"] == 8080][0]
        self.assertEqual((refused["result"], refused["errno"]), ("failed", "ECONNREFUSED"))
        self.assertTrue(by_ip["::1"]["allowed"])
        self.assertFalse(by_ip["8.8.8.8"]["allowed"], "unfinished/resumed не склеились")
        self.assertEqual(summary["unix_socket_calls"], 1)
        self.assertEqual(summary["status"], "warning")
        self.assertEqual(len(summary["violations"]), 2)

    def test_cidr_matching(self):
        networks = [dynamic.ipaddress.ip_network("10.0.0.0/8")]
        self.assertTrue(dynamic.is_allowed("10.1.2.3", networks))
        self.assertFalse(dynamic.is_allowed("11.0.0.1", networks))
        self.assertFalse(dynamic.is_allowed("::1", networks))
        self.assertTrue(dynamic.is_allowed("::ffff:10.1.2.3", networks))

    def test_install_summary(self):
        calls, exits = dynamic.parse_trace(SAMPLE_INSTALL)
        summary = dynamic.summarize_install(calls, exits, "/stage/files")

        self.assertEqual(summary["status"], "warning")
        self.assertIn("/stage/files/sky.dsk", summary["files_written"])
        self.assertEqual(summary["writes_outside_staging"], ["/home/user/.bashrc"])
        self.assertEqual(summary["renamed"], [{"from": "/stage/game.zip", "to": "/stage/files/game.zip"}])
        self.assertEqual(summary["files_deleted"], ["/stage/tmp"])
        self.assertEqual(summary["child_processes"]["spawned"], 1)
        self.assertEqual(summary["child_processes"]["failed_exits"][0]["exit_code"], 3)
        self.assertEqual(summary["errors"]["by_errno"], {"ENOENT": 1, "EACCES": 1})
        self.assertEqual([item["errno"] for item in summary["errors"]["samples"]], ["EACCES"])


@unittest.skipUnless(strace_works(), "strace недоступен или ptrace запрещён")
class StraceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server = TcpServer()
        self.addCleanup(self.server.close)

    def trace_client(self, **options):
        argv = [sys.executable, str(CLIENT), "127.0.0.1", str(self.server.port)]
        return dynamic.trace_network(argv, self.root / "reports", **options)

    def test_local_connection_appears_in_report(self):
        result = self.trace_client()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["status"], "ok")
        local = [c for c in result["connections"] if c["ip"] == "127.0.0.1"]
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0]["port"], self.server.port)
        self.assertEqual(local[0]["result"], "success")
        self.assertTrue(local[0]["allowed"])
        self.assertTrue(Path(result["trace_file"]).is_file())
        saved = json.loads(Path(result["summary_file"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["connections"], result["connections"])

    def test_refused_connection_is_reported(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        argv = [sys.executable, str(CLIENT), "127.0.0.1", str(port)]
        result = dynamic.trace_network(argv, self.root / "reports")
        self.assertEqual(result["exit_code"], 1)
        refused = [c for c in result["connections"] if c["port"] == port][0]
        self.assertEqual((refused["result"], refused["errno"]), ("failed", "ECONNREFUSED"))

    def test_connection_outside_policy_is_flagged(self):
        policy = self.root / "policy.json"
        policy.write_text(json.dumps({"allowed": ["10.0.0.0/8"]}), encoding="utf-8")
        result = self.trace_client(policy_path=policy)
        self.assertEqual(result["status"], "warning")
        self.assertFalse(result["violations"][0]["allowed"])

    def test_install_trace_records_files_and_children(self):
        stage = self.root / "stage"
        stage.mkdir()
        script = (
            "import pathlib, subprocess, sys;"
            "root = pathlib.Path(sys.argv[1]);"
            "(root / 'files').mkdir();"
            "(root / 'files' / 'sky.dsk').write_bytes(b'x');"
            "subprocess.run(['true']);"
            "open('/nonexistent/file')"
        )
        argv = [sys.executable, "-c", script, str(stage)]
        result = dynamic.trace_install(argv, stage / "files", self.root / "reports")

        self.assertNotEqual(result["exit_code"], 0)
        self.assertEqual(result["status"], "error")
        self.assertIn(str(stage / "files" / "sky.dsk"), result["files_written"])
        self.assertTrue(any(c["path"].endswith("true") for c in result["child_processes"]["executed"]))
        self.assertIn("ENOENT", result["errors"]["by_errno"])
        self.assertIn("FileNotFoundError", result["stderr_tail"])


if __name__ == "__main__":
    unittest.main()
