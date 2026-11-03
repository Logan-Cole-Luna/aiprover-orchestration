"""Validation of the orchestrator settings of a submission
(server/settings.py)."""

import unittest

from aiprover_orchestration.server import settings


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

if __name__ == "__main__":
    unittest.main()
