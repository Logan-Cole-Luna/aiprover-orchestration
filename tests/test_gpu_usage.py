"""GPU time from sampled server activity and open sessions."""

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiprover_orchestration.orchestrator import gpu_usage
from aiprover_orchestration.server.vista import GpuGroup
from aiprover_orchestration.utils.files import read_json


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

    def test_short_request_between_samples_counts(self):
        self.assertEqual(
            self.spans(sample(0, 0, 5), sample(30, 0, 6)), [[0, 30]]
        )

    def test_gap_in_sampling_breaks_the_span(self):
        self.assertEqual(
            self.spans(
                sample(0, 1), sample(30, 1), sample(1000, 1), sample(1030, 1)
            ),
            [[0, 30], [1000, 1030]],
        )


class ChargeTest(unittest.TestCase):
    def test_only_active_time_with_open_sessions(self):
        whole, share = gpu_usage.charged_hours(
            [[0, 3600, 2]], [], [[1800, 7200]]
        )
        self.assertEqual((whole, share), (0.5, 0.5))

    def test_overlapping_runs_sum_to_active_time(self):
        first = [[0, 3600, 3]]
        second = [[0, 3600, 1]]
        active = [[0, 3600]]
        _, share_first = gpu_usage.charged_hours(first, second, active)
        _, share_second = gpu_usage.charged_hours(second, first, active)
        self.assertEqual((share_first, share_second), (0.75, 0.25))


class RefreshTest(unittest.TestCase):
    def test_relative_path_is_not_its_own_neighbor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "results" / "run"
            run.mkdir(parents=True)
            spans = {"server": "dgx", "started": "2026-10-30",
                     "sessions": [[0, 60, 1]], "active": [[0, 60]],
                     "coverage": 1.0, "solver_calls": 1, "jobs": []}
            (run / gpu_usage.SPANS_FILE).write_text(json.dumps(spans))
            with contextlib.chdir(root), mock.patch.object(
                gpu_usage, "ROOT", root
            ), mock.patch.object(gpu_usage, "write_spans", return_value=spans):
                gpu_usage.refresh(Path("results/run"), {})
            usage = read_json(run / gpu_usage.USAGE_FILE)
            self.assertEqual(usage["charged_hours"], usage["active_hours"])


class SessionsTest(unittest.TestCase):
    def test_elapsed_clock_stops_while_a_run_is_down(self):
        trace = {
            "started": "2026-10-29T00:00:00",
            "resumptions": [
                {"elapsed_seconds": 100, "time": "2026-10-29T01:00:00"}
            ],
        }
        epoch = gpu_usage.epoch_of(trace)
        self.assertEqual(epoch(150) - epoch(50), 3600)


    def test_resumed_session_skips_the_outage(self):
        self.assertEqual(
            gpu_usage.running_spans([7200, 14400, 1], [(3600, 10800)]),
            [[10800, 14400, 1], [0, 3600, 1]],
        )

    def test_session_after_an_outage_is_unchanged(self):
        self.assertEqual(
            gpu_usage.running_spans([11000, 12000, 1], [(3600, 10800)]),
            [[11000, 12000, 1]],
        )

    def test_killed_session_ends_at_its_last_output(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "s0"
            session.mkdir()
            (session / ".hevo_state.json").write_text('{"t0": 100}')
            (session / "agent.log").write_text("")
            os.utime(session / "agent.log", (400, 400))
            self.assertEqual(
                gpu_usage.job_sessions(Path(directory)), [[100, 400, 1]]
            )

    def test_jobs_without_a_start_event_keep_their_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(gpu_usage, "ROOT", Path(directory)):
                sessions = gpu_usage.open_sessions(
                    {
                        "time": 1000.0,
                        "steps": [
                            {
                                "index": 0,
                                "kind": "model_call",
                                "backend": "aiprover",
                                "elapsed_seconds": 500,
                                "samples": [
                                    {"elapsed_sec": 300},
                                    {"elapsed_sec": 9, "status": "infra"},
                                ],
                            },
                            {
                                "index": 1,
                                "kind": "model_call",
                                "backend": "aiprover",
                                "elapsed_seconds": 600,
                                "samples": [{"elapsed_sec": 100}],
                            },
                        ],
                    }
                )
        self.assertEqual(sessions, [[1200.0, 1500.0, 1], [1500.0, 1600.0, 1]])

    @unittest.skipIf(os.geteuid() == 0, "root reads every directory")
    def test_other_accounts_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            store = root / "aiprover" / "work" / "jobs"
            results = root / "results"
            store.mkdir(parents=True)
            results.mkdir()
            store.chmod(0)
            results.chmod(0)
            try:
                with mock.patch.object(gpu_usage, "ROOT", root):
                    self.assertEqual(gpu_usage.job_stores(), [])
                    run = root / "users" / "me" / "results" / "run"
                    run.mkdir(parents=True)
                    self.assertEqual(gpu_usage.peers(run), [run])
            finally:
                store.chmod(0o700)
                results.chmod(0o700)


class ServerTest(unittest.TestCase):
    @staticmethod
    def trace(config):
        return {
            "config": {"agents": {"solver": {
                "backend": "aiprover", "config": config,
            }}}
        }

    def test_group_decides_over_the_file_name(self):
        with mock.patch.object(
            gpu_usage,
            "group_of_solver",
            return_value=GpuGroup("dgx", hardware="dgx"),
        ):
            self.assertEqual(
                gpu_usage.server_of(self.trace("device.toml")), "dgx"
            )

    def test_group_with_a_slurm_job_is_on_vista(self):
        trace = self.trace("configs/aiprover/vista_logged_openai.toml")
        self.assertEqual(gpu_usage.server_of(trace), "vista")

    def test_config_of_no_group_keeps_a_server(self):
        self.assertEqual(gpu_usage.server_of(self.trace("x/s3.toml")), "s3")


class ChargeRunsTest(unittest.TestCase):
    def test_an_import_does_not_compete_with_its_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            spans = {"run_id": "run", "server": "dgx",
                     "started": "2026-10-30", "sessions": [[0, 3600, 1]],
                     "active": [[0, 3600]], "coverage": 1.0, "solver_calls": 1,
                     "jobs": []}
            original = root / "run"
            imported = root / "imported_run_2"
            for path in (original, imported):
                path.mkdir()
            runs = {original: dict(spans), imported: dict(spans)}
            pool = gpu_usage.charging_pool(runs)
            self.assertNotIn(imported, pool)
            gpu_usage.charge(imported, runs[imported], pool)
            usage = read_json(imported / gpu_usage.USAGE_FILE)
            self.assertEqual(usage["charged_hours"], 1.0)

    def test_untraced_sessions_compete_only_with_unreferenced_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            store = root / "aiprover" / "work" / "jobs"
            for job, start in (("traced", 0), ("untraced", 0)):
                session = store / job / "s0"
                session.mkdir(parents=True)
                (session / ".hevo_state.json").write_text(f'{{"t0": {start}}}')
                (session / "resume.json").write_text('{"elapsed": 3600}')
            runs = {root / "run": {"jobs": ["traced"]}}
            with mock.patch.object(gpu_usage, "ROOT", root):
                pool = gpu_usage.charging_pool(runs)
            self.assertEqual(
                pool[root / "untraced"]["sessions"], [[0, 3600, 1]]
            )


class SharingTest(unittest.TestCase):
    def key(self, config):
        trace = {
            "config": {
                "agents": {"solver": {"backend": "aiprover", "config": config}}
            }
        }
        return gpu_usage.sharing_of(trace)

    def test_groups_of_one_hardware_do_not_share_a_server(self):
        self.assertEqual(self.key("configs/aiprover/vista_logged.toml"), "main")
        self.assertEqual(
            self.key("configs/aiprover/vista_logged_openai.toml"), "main"
        )
        self.assertEqual(
            self.key("configs/aiprover/vista_logged_open.toml"), "open"
        )

    def test_earlier_config_names_keep_their_group(self):
        self.assertEqual(self.key("aiprover/aiprover_vista.toml"), "main")
        self.assertEqual(
            self.key("aiprover/aiprover_vista_logged_open.toml"), "open"
        )

    def test_other_group_on_the_hardware_is_not_a_neighbor(self):
        run = {"server": "vista", "sharing": "open", "sessions": [[0, 60, 1]]}
        other = {"server": "vista", "sharing": "main", "sessions": [[0, 60, 1]]}
        peer = {"server": "vista", "sharing": "open", "sessions": [[0, 60, 1]]}
        self.assertEqual(
            list(
                gpu_usage.overlapping(
                    run, {Path("a"): other, Path("b"): peer}
                )
            ),
            [Path("b")],
        )


class UsageTest(unittest.TestCase):
    def usage(self, record):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / gpu_usage.USAGE_FILE).write_text(json.dumps(record))
            return gpu_usage.usage_of(path)

    def test_run_without_a_server(self):
        self.assertEqual(self.usage({"server": ""})["state"], "none")

    def test_unsampled_run(self):
        record = {
            "server": "dgx",
            "started": "2026-10-01",
            "solver_calls": 1,
            "recorded": False,
        }
        self.assertEqual(self.usage(record)["state"], "unrecorded")

    def test_pooled_run_is_a_lower_bound(self):
        record = {
            "server": "vista",
            "started": "2026-10-01",
            "solver_calls": 1,
            "recorded": True,
            "pooled": True,
            "coverage": 1.0,
            "charged_hours": 1.0,
        }
        self.assertEqual(self.usage(record)["state"], "partial")


if __name__ == "__main__":
    unittest.main()
