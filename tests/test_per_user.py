"""Per-user servers (scripts/user/aiprover-server): runtime state under
AIPROVER_ORCHESTRATION_HOME, the persistent group of the user's own proxy, run
processes of the own account only, run names and imports, and Claude roles
on the user page only on a per-user server."""

import json
import os
import unittest
import zipfile
from unittest import mock

from aiprover_orchestration import paths
from aiprover_orchestration.server import jobs
from aiprover_orchestration.server.jobs import JobStore
from aiprover_orchestration.server.model_servers import PERSISTENT_GROUP
from tests.support import ServerProcessTest, job

PROBLEM = {"statement": "theorem t : 1 = 1", "proof": "rfl", "gpu_group": PERSISTENT_GROUP}


class PerUserTest(ServerProcessTest):

    def test_user_page_takes_claude_roles_only_on_a_per_user_server(self):
        status, body = self.submit(PROBLEM)
        self.assertEqual(status, 400)
        self.assertIn("own Claude login", body["detail"])
        status, body = self.submit(PROBLEM, QUERY_SERVER_PER_USER="on")
        self.assertEqual(status, 201, body)


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


# Imports the zip given as argument and reports the listed run and its page.
IMPORT = """
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from aiprover_orchestration.server.app import app
client = TestClient(app)
page = sys.argv[2]
reply = client.post(
    f"/api/runs/import?page={page}", content=Path(sys.argv[1]).read_bytes()
)
if reply.status_code != 201:
    print(json.dumps({"status": reply.status_code}))
    sys.exit()
run = reply.json()["run_id"]
trace = client.get(f"/api/runs/{run}/files/trace.html")
print(json.dumps({
    "status": reply.status_code,
    "runs": client.get(f"/api/runs?page={page}").json(),
    "sandbox": trace.headers.get("content-security-policy"),
}))
"""


class ImportTest(ServerProcessTest):

    def archive(self, files: dict[str, str | bytes]) -> str:
        path = self.directory / "run.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return str(path)

    def trace(self) -> str:
        return json.dumps(
            {"run_id": "r1", "problem": {"uuid": "u1", "informal_statement": "A claim"}}
        )

    def test_user_page_does_not_resume_on_the_servers_login(self):
        path = self.archive({"trace.json": self.trace(), "trace.html": "page"})
        run = self.run_python(IMPORT, path, "user")["runs"][0]
        self.assertFalse(run["resumable"])

    def test_paths_outside_the_folder_are_refused(self):
        path = self.archive({"../escape": "x", "trace.html": "page"})
        self.assertEqual(self.run_python(IMPORT, path, "dev")["status"], 400)
        self.assertFalse((self.directory / "escape").exists())

class UserRunsTest(ServerProcessTest):

    def setUp(self) -> None:
        super().setUp()
        self.home = self.directory / "users" / "alice"
        JobStore(self.home / "state" / "jobs.db").add(job("r1", "running"))
        self.trace = self.home / "results" / "r1" / "trace.html"
        self.trace.parent.mkdir(parents=True)

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
