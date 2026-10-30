"""Orchestrator settings of a submission (server/settings.py): the fields
offered, their validation, and their storage as run options."""

import json
import unittest

from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.server import settings
from aiprover_orchestration.server.jobs import JobStore
from tests.support import ServerProcessTest

PROBLEM = {"statement": "theorem t : 1 = 1", "proof": "rfl", "page": "dev"}


class SettingsTest(unittest.TestCase):

    def test_settings_are_options_of_run_py(self):
        # run.py offers every scalar field of Config as an option; the worker
        # applies aiprover_timeout itself.
        names = set(settings.SETTINGS) - {"aiprover_timeout"}
        self.assertLessEqual(names, set(Config.__dataclass_fields__))

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

    def test_long_proofs_are_accepted(self):
        status, body = self.submit(PROBLEM | {"proof": "x" * 60_000})
        self.assertEqual(status, 201, body)

    def test_invalid_settings_are_rejected(self):
        status, body = self.submit(PROBLEM | {"settings": {"workers": 99}})
        self.assertEqual(status, 400)
        self.assertIn("workers", body["detail"])


if __name__ == "__main__":
    unittest.main()
