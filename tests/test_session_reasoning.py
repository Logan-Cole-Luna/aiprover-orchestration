"""Reasoning recovered from the transcript of an AIProver session."""

import unittest

from aiprover_orchestration.agents.aiprover import split_session_reasoning


def assistant(text):
    return {"role": "assistant", "text": text}


class SplitSessionReasoningTest(unittest.TestCase):
    def test_tags_move_to_the_turn_and_keep_the_record(self):
        sample = {
            "session": [assistant("[THINK]First.[/THINK] Answer")],
            "reasoning": [{"turn": 1, "time": "13:58:13", "reasoning": ""}],
        }
        split_session_reasoning(sample)
        self.assertEqual(sample["session"][0]["text"], "Answer")
        self.assertEqual(
            sample["reasoning"],
            [{"turn": 1, "time": "13:58:13", "reasoning": "First."}],
        )

if __name__ == "__main__":
    unittest.main()
