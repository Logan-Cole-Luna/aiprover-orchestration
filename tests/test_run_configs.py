"""The run configurations of configs/orchestrator/ are valid, and their comments
(keys that start with an underscore) are ignored."""

import json
import unittest
from dataclasses import fields

from aiprover_orchestration.orchestrator.config import Config, current_names
from aiprover_orchestration.paths import CONFIG_DIR

PROVIDED = ("default", "aiprover", "claude", "codex", "codex_aiprover")


class RunConfigTest(unittest.TestCase):
    def test_each_provided_configuration_builds_a_config_without_its_comments(self):
        names = {field.name for field in fields(Config)}
        for name in PROVIDED:
            with self.subTest(name):
                settings = current_names(json.loads((CONFIG_DIR / "orchestrator" / f"{name}.json").read_text()))
                self.assertLessEqual(set(settings), names)
                self.assertEqual(set(Config(**settings).agents), {"orchestrator", "checker", "reviewer", "writer", "prover"})

    def test_every_option_of_the_comments_is_a_key_of_the_file(self):
        for name in PROVIDED:
            raw = json.loads((CONFIG_DIR / "orchestrator" / f"{name}.json").read_text())
            self.assertEqual(set(raw["_options"]), {k for k in raw if not k.startswith("_") and k != "agents"}, name)


if __name__ == "__main__":
    unittest.main()
