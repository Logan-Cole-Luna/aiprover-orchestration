"""Requests of the query server on a project: creating it, editing the plan
of its results, launching it, and deciding on its claims."""

from tests.support import ServerProcessTest

FLOW = """
import json
from fastapi.testclient import TestClient
from aiprover_orchestration.orchestrator import project
from aiprover_orchestration.server.app import app, store
client = TestClient(app)
dgx = {"provider": "aiprover", "model": "dgx"}
new = {"title": "A: Test", "gpu_group": "dgx", "subagent": dgx}
page = "?page=dev"
replies = [client.post("/api/projects" + page, json=new).status_code,
           client.post("/api/projects" + page, json=new).status_code]
path = "/api/projects/a_test"
one = {"uuid": "one", "statement": "S"}
replies.append(client.put(path + "/members" + page, json=[one, one]).status_code)
replies.append(client.put(path + "/members", json=[one]).status_code)
replies.append(client.put(path + "/members" + page, json=[one]).status_code)
replies.append(client.post(path + "/launch" + page).json())
replies.append(
    client.put(path + "/members" + page, json=[{"uuid": "two"}]).status_code
)
project.update("a_test", lambda p: p["reviews"].append({"claim": "C"}))
decision = {"decision": "rejected", "note": "false"}
replies.append(client.post(path + "/reviews/0" + page, json=decision).status_code)
replies.append(client.post(path + "/reviews/1" + page, json=decision).status_code)
print(json.dumps([replies, store.get("one"), project.load("a_test")]))
"""


class ProjectRequestTest(ServerProcessTest):
    def test_a_project_from_creation_to_review(self):
        replies, run, project = self.run_python(FLOW)
        self.assertEqual(replies, [
            201, 400, 400, 404, 200, {"started": ["one"]}, 200, 200, 404,
        ])
        self.assertEqual(run["owner"], "guest")
        self.assertIn("libraries/a_test.lean", run["options"])
        names = [member["uuid"] for member in project["members"]]
        self.assertEqual(names, ["one", "two"])
        self.assertEqual(project["reviews"][0]["decision"], "rejected")
        self.assertEqual(project["reviews"][0]["by"], "guest")
