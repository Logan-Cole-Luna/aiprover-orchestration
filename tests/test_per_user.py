"""Per-user servers (scripts/user/aiprover-server): runtime state under
AIPROVER_ORCHESTRATION_HOME, the DGX group of the user's own proxy, run
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


# Prints the group of each job given as argument.
GROUPS = """
import json, sys
from aiprover_orchestration.server.app import problem_group
print(json.dumps([problem_group(job) for job in json.loads(sys.argv[1])]))
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

    def test_export_layout_is_restored(self):
        path = self.archive({
            "r1/trace.html": "page",
            "r1/report_informal.pdf": "pdf",
            "r1/run_data/trace.json": self.trace(),
            "r1/run_data/summary.json": '{"status": "lemmas_unproved"}',
            "r1/run_data/problem.json": "{}",
            "r1/lean_modules/Def_a.lean": "def",
            "r1/logs/calls.jsonl": "log",
        })
        imported = self.run_python(IMPORT, path, "dev")
        run = imported["runs"][0]
        self.assertEqual(run["run_id"], "imported_r1")
        self.assertEqual(
            (run["title"], run["status"]), ("A claim", "lemmas_unproved")
        )
        self.assertTrue(run["resumable"])
        self.assertEqual(imported["sandbox"], "sandbox allow-scripts")
        folder = self.directory / "results" / "imported_r1"
        self.assertEqual(
            sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()),
            ["Def_a.lean", "informal/report_informal.pdf", "summary.json",
             "trace.html", "trace.json"],
        )

    def test_user_page_does_not_resume_on_the_servers_login(self):
        path = self.archive({"trace.json": self.trace(), "trace.html": "page"})
        run = self.run_python(IMPORT, path, "user")["runs"][0]
        self.assertFalse(run["resumable"])

    def test_paths_outside_the_folder_are_refused(self):
        path = self.archive({"../escape": "x", "trace.html": "page"})
        self.assertEqual(self.run_python(IMPORT, path, "dev")["status"], 400)
        self.assertFalse((self.directory / "escape").exists())

    def test_invalid_records_are_refused_without_leaving_files(self):
        path = self.archive({"trace.json": "[1]", "trace.html": "page"})
        self.assertEqual(self.run_python(IMPORT, path, "dev")["status"], 400)
        self.assertEqual(list((self.directory / "results").glob("*")), [])

    def test_a_run_takes_its_group_or_that_of_its_dataset(self):
        named = job("r1", "queued") | {"problem_group": "My book"}
        dataset = job("r2", "queued") | {"uuid": "OpenAIMath_001_X"}
        (self.directory / "state").mkdir(exist_ok=True)
        (self.directory / "state" / "groups.json").write_text('{"OpenAIMath_": "OpenAI math"}')
        self.assertEqual(self.run_python(GROUPS, json.dumps([named, dataset, job("r3", "queued")])), ["My book", "OpenAI math", ""])

    def test_run_name_becomes_the_id(self):
        body = PROBLEM | {"name": "my run/1"}
        status, reply = self.submit(body, QUERY_SERVER_PER_USER="on")
        self.assertEqual((status, reply["run_id"]), (201, "my_run_1"))
        status, reply = self.submit(body, QUERY_SERVER_PER_USER="on")
        self.assertEqual((status, reply["run_id"]), (201, "my_run_1_2"))


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
