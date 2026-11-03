"""GPU time from sampled server activity and open sessions."""

import json
import tempfile
import unittest
from pathlib import Path

from aiprover_orchestration.orchestrator import gpu_usage


def sample(moment, running, finished=0):
    return json.dumps(
        {
            "time": moment,
            "server": {
                gpu_usage.RUNNING: running,
                gpu_usage.FINISHED: finished,
            },
        }
    )


class ActiveSpansTest(unittest.TestCase):
    def spans(self, *lines):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            path.write_text("\n".join(lines))
            return gpu_usage.sampled_spans(path)[1]

    def test_idle_time_is_not_counted(self):
        self.assertEqual(
            self.spans(
                sample(0, 1),
                sample(30, 1),
                sample(60, 0),
                sample(90, 0),
                sample(120, 0),
            ),
            [[0, 60]],
        )

class SessionsTest(unittest.TestCase):


    def test_resumed_session_skips_the_outage(self):
        self.assertEqual(
            gpu_usage.running_spans([7200, 14400, 1], [(3600, 10800)]),
            [[10800, 14400, 1], [0, 3600, 1]],
        )

if __name__ == "__main__":
    unittest.main()
