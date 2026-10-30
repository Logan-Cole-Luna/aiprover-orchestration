"""Per-user servers (scripts/user/aiprover-server): runtime state under
AIPROVER_ORCHESTRATION_HOME, the DGX group of the user's own proxy, run
processes of the own account only, and Claude roles on the user page only
on a per-user server."""

import os
import unittest
from unittest import mock

from aiprover_orchestration import paths
from aiprover_orchestration.server import jobs
from aiprover_orchestration.server.jobs import JobStore
from tests.support import ServerProcessTest, job

PROBLEM = {"statement": "theorem t : 1 = 1", "proof": "rfl", "gpu_group": "dgx"}

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

# Prints what /api/me reports.
ME = """
import json
from fastapi.testclient import TestClient
from aiprover_orchestration.server.app import app
print(json.dumps(TestClient(app).get("/api/me").json()))
"""


class PerUserTest(ServerProcessTest):

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
        status, body = self.submit(PROBLEM)
        self.assertEqual(status, 400)
        self.assertIn("own Claude login", body["detail"])
        status, body = self.submit(PROBLEM, QUERY_SERVER_PER_USER="on")
        self.assertEqual(status, 201, body)


    def test_only_a_per_user_server_shows_its_claude_account(self):
        me = self.run_python(ME)
        self.assertEqual((me["per_user"], me["claude_account"]), (False, None))


# Lists the development page's runs and tries one read and one action on
# the user's run given as argument.
USER_RUN = """
import json, sys
from fastapi.testclient import TestClient
from aiprover_orchestration.server.app import app
client = TestClient(app)
run = sys.argv[1]
print(json.dumps({
    "dev": [r["run_id"] for r in client.get("/api/runs?page=dev").json()],
    "user": [r["run_id"] for r in client.get("/api/runs?page=user").json()],
    "trace": client.get(f"/api/runs/{run}/files/trace.html").text,
    "cancel": client.delete(f"/api/runs/{run}").status_code,
}))
"""


class UserRunsTest(ServerProcessTest):

    def setUp(self) -> None:
        super().setUp()
        self.home = self.directory / "users" / "alice"
        JobStore(self.home / "state" / "jobs.db").add(job("r1", "running"))
        self.trace = self.home / "results" / "r1" / "trace.html"
        self.trace.parent.mkdir(parents=True)

    def test_development_page_lists_user_runs_read_only(self):
        self.trace.write_text("alice's trace")
        listed = self.run_python(USER_RUN, "alice:r1")
        self.assertEqual(listed["dev"], ["alice:r1"])
        self.assertEqual(listed["user"], [])
        self.assertEqual(listed["trace"], "alice's trace")
        self.assertEqual(listed["cancel"], 403)

    def test_links_out_of_a_user_state_are_not_served(self):
        (self.directory / "secret").write_text("the owner's secret")
        self.trace.symlink_to(self.directory / "secret")
        listed = self.run_python(USER_RUN, "alice:r1")
        self.assertNotIn("secret", listed["trace"])


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
