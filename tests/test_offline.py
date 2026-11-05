"""Sessions kept off the internet: lean/offline.py under Landlock."""

import socket
import subprocess
import sys
import textwrap
import unittest

from aiprover_orchestration.agents.aiprover import OFFLINE, offline_prefix


def offline(port: int, code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(OFFLINE), str(port), "--",
         sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )  # fmt: skip


CONNECT = "import socket; socket.create_connection(('127.0.0.1', {}), 5)"


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.allowed = socket.create_server(("127.0.0.1", 0))
        self.other = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(self.allowed.close)
        self.addCleanup(self.other.close)
        self.port = self.allowed.getsockname()[1]
        self.other_port = self.other.getsockname()[1]

    def check_listed_port_connects(self):
        done = offline(self.port, CONNECT.format(self.port))
        self.assertEqual(done.returncode, 0, done.stderr)

    def check_other_ports_are_refused(self):
        for port in (self.other_port, 443, 80):
            done = offline(self.port, CONNECT.format(port))
            self.assertIn("PermissionError", done.stderr)

    def check_detached_children_stay_confined(self):
        child = CONNECT.format(self.other_port)
        code = textwrap.dedent(f"""
            import subprocess, sys
            done = subprocess.run(
                [sys.executable, "-I", "-c", {child!r}],
                capture_output=True, text=True, start_new_session=True,
            )
            print(done.stderr)
        """)
        self.assertIn("PermissionError", offline(self.port, code).stdout)

    def test_checks(self):
        for index, name in enumerate([
            "check_listed_port_connects",
            "check_other_ports_are_refused",
            "check_detached_children_stay_confined",
        ]):
            if index:
                self.tearDown()
                self.setUp()
            with self.subTest(name):
                getattr(self, name)()


class PrefixTest(unittest.TestCase):
    def test_ports_come_from_the_harness_configuration(self):
        harness = {
            "endpoint": {"api_base": "http://127.0.0.1:18566/v1"},
            "runtime": {"leanfinder_url": "http://127.0.0.1:18570"},
        }
        self.assertIn("18566,18570", offline_prefix(harness))


if __name__ == "__main__":
    unittest.main()
