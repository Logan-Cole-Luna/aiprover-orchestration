"""Per-user servers (scripts/user/aiprover-server): runtime state under
AIPROVER_ORCHESTRATION_HOME, the DGX group of the user's own proxy, run
processes of the own account only, and Claude roles on the user page only
on a per-user server."""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from aiprover_orchestration import paths
from aiprover_orchestration.server import jobs
from tests.support import TemporaryDirectoryTest

# Submits a problem from the user page of a server whose state lies in the
# directory given as argument, and prints the response status and body.
SUBMIT = """
import json, sys
from fastapi.testclient import TestClient
from aiprover_orchestration.server.app import app
response = TestClient(app).post(
    "/api/runs",
    json={"statement": "theorem t : 1 = 1", "proof": "rfl", "gpu_group": "dgx"},
)
print(json.dumps([response.status_code, response.json()]))
"""

# Prints the module paths and DGX group of a fresh interpreter.
LOCATIONS = """
import json
from aiprover_orchestration import paths
from aiprover_orchestration.server import models, vista
dgx = vista.GPU_GROUPS["dgx"]
print(json.dumps({
    "results": str(paths.RESULTS_DIR),
    "data": str(paths.DATA_DIR),
    "library": str(paths.locate("libraries/a.lean")),
    "dataset": str(paths.locate("data/a.jsonl")),
    "dgx_config": str(dgx.tunnel_config),
    "dgx_port": dgx.proxy_port,
    "slots": models.VERSION_SETTINGS["dgx"]["aiprover_session_slots"],
}))
"""


class PerUserTest(TemporaryDirectoryTest):

    def run_python(self, code: str, **environment) -> object:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("AIPROVER_", "QUERY_SERVER_"))
        }
        env |= {"AIPROVER_ORCHESTRATION_HOME": str(self.directory)}
        env |= environment
        process = subprocess.run(
            [sys.executable, "-c", code],
            cwd=paths.ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(process.stdout.splitlines()[-1])

    def test_state_and_dgx_group_follow_the_environment(self):
        config = self.directory / "dgx.toml"
        located = self.run_python(
            LOCATIONS,
            AIPROVER_DGX_CONFIG=str(config),
            AIPROVER_DGX_PROXY_PORT="40001",
            AIPROVER_MAX_PARALLEL="12",
        )
        self.assertEqual(located["results"], str(self.directory / "results"))
        self.assertEqual(located["data"], str(paths.ROOT / "data"))
        self.assertEqual(
            located["library"], str(self.directory / "libraries" / "a.lean")
        )
        self.assertEqual(located["dataset"], str(paths.ROOT / "data" / "a.jsonl"))
        self.assertEqual(located["dgx_config"], str(config))
        self.assertEqual(located["dgx_port"], 40001)
        self.assertEqual(located["slots"], 12)

    def test_user_page_takes_claude_roles_only_on_a_per_user_server(self):
        status, body = self.run_python(SUBMIT)
        self.assertEqual(status, 400)
        self.assertIn("own Claude login", body["detail"])
        status, body = self.run_python(SUBMIT, QUERY_SERVER_PER_USER="on")
        self.assertEqual(status, 201, body)


class RunProcessTest(unittest.TestCase):

    def test_processes_of_other_accounts_are_ignored(self):
        other = mock.Mock(pid=1, info={"uids": mock.Mock(real=os.getuid() + 1)})
        with mock.patch.object(
            jobs.psutil, "process_iter", return_value=[other]
        ), mock.patch.object(jobs, "process_alive", return_value=True):
            self.assertFalse(jobs.run_process_alive("r1"))
            other.info["uids"].real = os.getuid()
            self.assertTrue(jobs.run_process_alive("r1"))


if __name__ == "__main__":
    unittest.main()
