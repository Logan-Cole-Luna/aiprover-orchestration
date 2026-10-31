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

    def test_turn_without_a_record_is_added_in_order(self):
        sample = {
            "session": [
                {"role": "user", "text": "Task"},
                assistant(""),
                assistant("[THINK]Second."),
            ],
            "reasoning": [{"turn": 1, "reasoning": "First."}],
        }
        split_session_reasoning(sample)
        self.assertEqual(
            [(r["turn"], r["reasoning"]) for r in sample["reasoning"]],
            [(1, "First."), (2, "Second.")],
        )

    def test_empty_record_is_dropped(self):
        sample = {
            "session": [assistant("Answer")],
            "reasoning": [{"turn": 1, "reasoning": ""}],
        }
        split_session_reasoning(sample)
        self.assertEqual(sample["reasoning"], [])

    def test_recorded_reasoning_is_not_repeated(self):
        sample = {
            "session": [assistant("[THINK]Same.[/THINK]")],
            "reasoning": [{"turn": 1, "reasoning": "Same."}],
        }
        split_session_reasoning(sample)
        self.assertEqual(len(sample["reasoning"]), 1)
        self.assertEqual(sample["session"][0]["text"], "")

    def test_reasoning_beside_the_record_is_kept(self):
        sample = {
            "session": [assistant("[THINK]B[/THINK] Answer")],
            "reasoning": [{"turn": 1, "reasoning": "A"}],
        }
        split_session_reasoning(sample)
        self.assertEqual(sample["reasoning"][0]["reasoning"], "A\n\nB")
        self.assertEqual(sample["session"][0]["text"], "Answer")

    def test_empty_tags_leave_the_text(self):
        sample = {"session": [assistant("[THINK][/THINK]Answer")]}
        split_session_reasoning(sample)
        self.assertEqual(sample["session"][0]["text"], "Answer")


if __name__ == "__main__":
    unittest.main()
