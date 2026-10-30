"""Orchestrator settings of a submission (server/settings.py): their
validation and their storage as run options."""

import json
import unittest

from aiprover_orchestration.server import settings
from aiprover_orchestration.server.jobs import JobStore
from tests.support import ServerProcessTest

PROBLEM = {"statement": "theorem t : 1 = 1", "proof": "rfl", "page": "dev"}


class SettingsTest(unittest.TestCase):

    def test_values_are_checked(self):
        accepted = {"max_handbacks": 5, "sketch_judge": False,
                    "aiprover_direction": ""}
        self.assertEqual(settings.validate(accepted), accepted)
        for rejected in (
            {"workers": 0},
            {"workers": 2.5},
            {"workers": True},
            {"sketch_judge": 1},
            {"aiprover_direction": "other"},
            {"lean_parallel": 8},
        ):
            with self.assertRaises(ValueError):
                settings.validate(rejected)


class SubmissionTest(ServerProcessTest):

    def options(self, run_id: str) -> dict:
        records = JobStore(self.directory / "state" / "jobs.db", readonly=True)
        return json.loads(records.get(run_id)["options"])

    def test_settings_are_stored_as_run_options(self):
        chosen = {"max_handbacks": 3, "aiprover_max_retries": 4}
        status, body = self.submit(PROBLEM | {"settings": chosen})
        self.assertEqual(status, 201, body)
        self.assertEqual(self.options(body["run_id"]), chosen)


if __name__ == "__main__":
    unittest.main()
