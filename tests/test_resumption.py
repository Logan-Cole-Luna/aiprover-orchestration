"""Requests of the query server on an ended run: going past a Claude limit,
writing its documents, and deciding on its proof plan."""

import json

from tests.support import ServerProcessTest, job

# Adds a run (argument: its fields), writes its summary and costs, makes the
# request, and prints the response and the run's stored options.
REQUEST = """
import json, sys
from fastapi.testclient import TestClient
from aiprover_orchestration.server.app import RESULTS_DIR, app, store
record, limit, cost, path, body = map(json.loads, sys.argv[1:6])
store.add(record)
run = RESULTS_DIR / record["run_id"]
run.mkdir(parents=True)
(run / "trace.json").write_text("{}")
(run / "summary.json").write_text(
    json.dumps({"config": {"max_claude_usd": limit}})
)
(run / "benchmark.json").write_text(
    json.dumps({"costs": {"hosted_cost_usd": cost}})
)
reply = TestClient(app).post(path, json=body)
stored = store.get(record["run_id"])
print(json.dumps({
    "status": reply.status_code,
    "run": reply.json(),
    "options": json.loads(stored["options"] or "{}"),
    "state": stored["state"],
}))
"""


class RequestTest(ServerProcessTest):
    def post(self, state, status, path, body=None, cost=1, **fields):
        record = job("r", state) | {"status": status} | fields
        arguments = (record, 50, cost, f"/api/runs/r/{path}", body)
        return self.run_python(REQUEST, *map(json.dumps, arguments))


class ResumptionTest(RequestTest):
    def test_a_run_at_its_limit_needs_a_higher_one(self):
        reply = self.post("finished", "failed", "resume", cost=60)
        self.assertEqual(reply["status"], 409)
        self.assertEqual(reply["state"], "finished")

    def test_a_higher_limit_resumes_the_run(self):
        body = {"max_claude_usd": 200}
        reply = self.post("finished", "failed", "resume", body, cost=60)
        self.assertEqual(reply["state"], "queued")
        self.assertEqual(reply["options"], body)

    def test_new_choices_reach_the_run(self):
        body = {"gpu_group": "dgx", "findings": "a bound is known"}
        reply = self.post("cancelled", "failed", "resume", body)
        self.assertEqual(reply["run"]["gpu_group"], "dgx")
        self.assertEqual(
            reply["options"],
            {"sketch_feedback": "a bound is known", "plan_revision": 1},
        )

class ReportRequestTest(RequestTest):
    def test_a_proved_run_queues_for_its_documents(self):
        reply = self.post("finished", "proved", "report")
        self.assertEqual(reply["state"], "queued")
        self.assertEqual(
            reply["options"], {"write_reports": True, "documents_only": True}
        )

class PlanDecisionTest(RequestTest):
    def decide(self, decision, comments="", state="plan_review", **fields):
        body = {"decision": decision, "comments": comments}
        return self.post(state, "plan_review", "plan", body, **fields)

    def test_approval_starts_proving(self):
        reply = self.decide("approve")
        self.assertEqual(reply["state"], "queued")
        self.assertIs(reply["options"]["sketch_approval"], False)

    def test_comments_go_to_the_captain_as_guidance(self):
        reply = self.decide("comment", "prefer induction")
        self.assertEqual(reply["state"], "queued")
        self.assertEqual(reply["options"]["sketch_guidance"], "prefer induction")

    def test_a_revision_returns_for_review(self):
        reply = self.decide("revise", "split the lemma")
        self.assertEqual(
            reply["options"],
            {"sketch_feedback": "split the lemma", "plan_revision": 1},
        )

    def test_an_imported_run_needs_a_server_that_may_continue_it(self):
        reply = self.decide("approve", imported="view")
        self.assertEqual(reply["status"], 409)
