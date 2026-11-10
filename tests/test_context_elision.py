"""Tool results of an OpenAI-compatible agent are dropped from its history once
the prompt nears the context length (agents/openai_compatible.py)."""

import unittest

from aiprover_orchestration.agents.openai_compatible import (
    ELIDED_REPLY,
    KEPT_TOOL_REPLIES,
    OpenAICompatibleAgent,
)


class FakeSession:
    def tools(self):
        return [{"name": "check", "description": "", "inputSchema": {}}]

    def answer(self, _method, _params):
        return {"content": [{"text": "x" * 100}]}


class ElisionTest(unittest.TestCase):
    def converse(self, max_context):
        agent = OpenAICompatibleAgent("m", base_url="http://x/v1", tools=True, max_turns=50, max_context=max_context)
        replies = iter([{"role": "assistant", "tool_calls": [{"id": str(i), "function": {"name": "check"}}]} for i in range(8)]
                       + [{"role": "assistant", "content": "done"}])
        agent._post = lambda *_: {"choices": [{"message": next(replies)}], "usage": {"prompt_tokens": 900}}
        messages = [{"role": "user", "content": "go"}]
        agent._converse(messages, FakeSession())
        return [m["content"] for m in messages if m["role"] == "tool"]

    def test_old_results_are_dropped_near_the_limit_and_kept_otherwise(self):
        dropped = self.converse(1000)
        # The latest call is answered after the history is trimmed.
        self.assertEqual(dropped.count(ELIDED_REPLY), 8 - KEPT_TOOL_REPLIES - 1)
        self.assertNotIn(ELIDED_REPLY, self.converse(0))
        self.assertNotIn(ELIDED_REPLY, self.converse(10_000))


if __name__ == "__main__":
    unittest.main()
