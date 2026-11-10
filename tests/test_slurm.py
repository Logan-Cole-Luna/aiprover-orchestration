"""Runs as Slurm jobs (server/slurm.py), their egress (server/egress_proxy.py),
and the worker's use of them (server/jobs.py), against stand-ins for the Slurm
commands and a cluster that is the machine itself."""

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

from tests.support import ROOT, ServerProcessTest

# Each stand-in logs its arguments and answers from files in $FAKE_SLURM.
FAKES = {
    "sbatch": 'echo "$*" >> "$FAKE_SLURM/sbatch.log"; echo 4242; echo "sbatch: NOTE: a site note" >&2',
    "scancel": 'echo "$*" >> "$FAKE_SLURM/scancel.log"',
    "sacct": 'cat "$FAKE_SLURM/sacct" 2>/dev/null',
    "squeue": textwrap.dedent(
        """\
        case "$*" in
          *" -n "*) cat "$FAKE_SLURM/servers" 2>/dev/null ;;
          *) cat "$FAKE_SLURM/queue" 2>/dev/null ;;
        esac"""
    ),
    "submit_server.sh": 'echo "$* $SERVED_NAME" >> "$FAKE_SLURM/submit.log"; echo "Submitted batch job 9001"',
}

SUBMIT = """
import json, os, stat
from pathlib import Path
from aiprover_orchestration.server import slurm
from aiprover_orchestration.paths import TEMP_DIR

job = slurm.submit_run(
    "r1", ["python", "-c", "pass", "--config", "/x.json"],
    {"SECRET": "secret"}, TEMP_DIR / "r1.out", "g", "after:7",
)
spec = slurm.spec_path("r1")
print(json.dumps([
    job, oct(stat.S_IMODE(spec.stat().st_mode)), json.loads(spec.read_text()),
    (Path(os.environ["FAKE_SLURM"]) / "sbatch.log").read_text(),
]))
"""

JOB = """
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from aiprover_orchestration.server import slurm
from aiprover_orchestration.paths import TEMP_DIR

class Model(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "aiprover", "max_model_len": 65536}]}).encode())

    def log_message(self, *_):
        pass

server = HTTPServer(("127.0.0.1", 0), Model)
threading.Thread(target=server.serve_forever, daemon=True).start()
fake = Path(os.environ["FAKE_SLURM"])
(fake / "handoff").write_text(f"node=127.0.0.1 port={server.server_port} job=777\\n")
(fake / "queue").write_text(sys.argv[1])
TEMP_DIR.mkdir(parents=True, exist_ok=True)
config = TEMP_DIR / "r2.config.json"
config.write_text(json.dumps({"agents": {
    "captain": {"backend": "openai_compatible", "model": "aiprover", "base_url": "http://127.0.0.1:18575/v1"},
    "solver": {"backend": "aiprover", "model": "aiprover", "config": "configs/aiprover/slurm.toml"},
}}))
seen = TEMP_DIR / "r2.seen.json"
names = ("AIPROVER_CONFIG", "AIPROVER_WORKSPACE", "AGENT_MAX_CONTEXT", "NO_PROXY", "HTTPS_PROXY", "AIPROVER_LIMITS_DIR")
child = f"import json, os; json.dump({{k: os.environ.get(k, '') for k in {names}}}, open({str(seen)!r}, 'w'))"
slurm.spec_path("r2").write_text(json.dumps({
    "run_id": "r2", "arguments": [sys.executable, "-c", child, "--config", str(config)],
    "environment": {"AIPROVER_CONFIG": "configs/aiprover/slurm.toml"}, "config": str(config), "group": "g",
}))
code = slurm.job_main(slurm.spec_path("r2"))
agents = json.loads(config.read_text())["agents"]
print(json.dumps([
    code, slurm.outcome("r2"), agents["captain"]["base_url"], agents["solver"]["config"],
    Path(agents["solver"]["config"]).read_text(), json.loads(seen.read_text()) if seen.exists() else None,
]))
"""

BATCH = """
import json, os, sys
from pathlib import Path
from aiprover_orchestration.paths import TEMP_DIR
from aiprover_orchestration.server import model_servers, slurm

# A login shell resets PATH, which hides the stand-ins.
model_servers.remote = lambda command, timeout=60: model_servers.run_command(["bash", "-c", command], timeout)
fake = Path(os.environ["FAKE_SLURM"])
(fake / "run.json").write_text(json.dumps({"agents": {
    "captain": {"backend": "openai_compatible", "model": "aiprover", "base_url": "http://127.0.0.1:18575/v1"},
    "solver": {"backend": "aiprover", "model": "aiprover", "config": "another/harness.toml"},
}}))
(fake / "servers").write_text(sys.argv[1])
run_id = sys.argv[2]
sys.argv = [
    "slurm", "submit", "--problem-uuid", "OpenAIMath_164_Hindman",
    "--config", str(fake / "run.json"), "--run-id", run_id,
]
slurm.main()
print(json.dumps([
    (fake / "sbatch.log").read_text().splitlines()[-1],
    (fake / "submit.log").read_text().splitlines() if (fake / "submit.log").exists() else [],
    json.loads((TEMP_DIR / f"{run_id}.config.json").read_text())["agents"]["solver"]["config"],
]))
"""

EGRESS = """
import asyncio, json, tempfile
from pathlib import Path
from aiprover_orchestration.server import egress_proxy

egress_proxy.ALLOWED_HOSTS.append("*.anthropic.com")

async def ask(sock, request):
    reader, writer = await asyncio.open_unix_connection(sock)
    writer.write(request)
    await writer.drain()
    reply = await reader.read(64)
    writer.close()
    return reply.decode().split("\\r\\n")[0]

async def main():
    sock = Path(tempfile.mkdtemp()) / "egress.sock"
    server = asyncio.create_task(egress_proxy.serve(sock))
    for _ in range(50):
        if sock.exists():
            break
        await asyncio.sleep(0.05)
    mode = oct(sock.stat().st_mode & 0o777)
    replies = [
        await ask(sock, b"CONNECT evil.example.com:443 HTTP/1.1\\r\\n\\r\\n"),
        await ask(sock, b"CONNECT api.anthropic.com:80 HTTP/1.1\\r\\n\\r\\n"),
        await ask(sock, b"GET http://api.anthropic.com/ HTTP/1.1\\r\\n\\r\\n"),
    ]
    server.cancel()
    return mode, replies

mode, replies = asyncio.run(main())
print(json.dumps([
    mode, replies, egress_proxy.allowed("API.Anthropic.com"), egress_proxy.allowed("anthropic.com.evil.net"),
    egress_proxy.parse_connect(b"CONNECT [::1]:443 HTTP/1.1\\r\\n\\r\\n"),
]))
"""

HOSTS = """
import json
from aiprover_orchestration.server import slurm

print(json.dumps(slurm.hosted_hosts([
    {"backend": "claude", "phases": {"escalation": {"backend": "codex"}}},
    {"backend": "openai_compatible", "base_url": "https://api.example.com/v1"},
    {"backend": "openai_compatible", "base_url": "http://127.0.0.1:18575/v1"},
    {"backend": "openai_compatible", "base_url": "http://node/v1", "group": "o"},
])))
"""

DEFAULT_SITE = """
import json
from aiprover_orchestration.server import model_servers as servers

group = servers.GPU_GROUPS["slurm"]
print(json.dumps([servers.DEFAULT_GROUP, servers.LOCAL_CLUSTER, servers.CHECKPOINTS, group.job_name, group.checkpoint]))
"""

WORKER = """
import json, os, time
from pathlib import Path
from aiprover_orchestration.server import jobs, slurm
from aiprover_orchestration.paths import RESULTS_DIR

store = jobs.JobStore()
worker = jobs.Worker(store)
fake = Path(os.environ["FAKE_SLURM"])
for run_id in ("w1", "w2"):
    store.add({"run_id": run_id, "owner": "u", "uuid": "OpenAIMath_164_Hindman", "title": "t",
               "dataset": "data/openai_math.jsonl", "state": "queued", "submitted": time.time(), "gpu_group": "cpu"})
    worker._execute(store.get(run_id))
started = [store.get(r)["slurm_job"] for r in ("w1", "w2")]

# Slurm stopped w1 before its wall time; w2 ended on its own.
slurm.write_private(slurm.outcome_path("w1"), json.dumps({"outcome": "exited", "signalled": True}))
slurm.write_private(slurm.outcome_path("w2"), json.dumps({"outcome": "exited", "signalled": False}))
(RESULTS_DIR / "w2").mkdir(parents=True)
(RESULTS_DIR / "w2" / "summary.json").write_text(json.dumps({"status": "proved"}))
(fake / "sacct").write_text("COMPLETED\\n")
(fake / "queue").write_text("")
slurm._queue[:] = [0.0, None]
worker._reap()
after = {r: {k: store.get(r)[k] for k in ("state", "status", "slurm_job", "resumptions")} for r in ("w1", "w2")}

# A cancelled run job gets SIGTERM through its driver.
store.update("w1", state="running", slurm_job="4243")
(fake / "queue").write_text("4243|aiprover_run_w1|RUNNING\\n")
slurm._queue[:] = [0.0, None]
cancelled = worker.cancel("w1")
print(json.dumps([started, after, cancelled, (fake / "scancel.log").read_text().splitlines()]))
"""


class SlurmTest(ServerProcessTest):
    def setUp(self):
        super().setUp()
        self.fake = self.directory / "fake_slurm"
        bin_dir = self.fake / "bin"
        bin_dir.mkdir(parents=True)
        for name, body in FAKES.items():
            path = bin_dir / name
            path.write_text("#!/bin/bash\n" + body + "\n")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        # A cluster without a host is this machine; group g is served on it.
        site = {
            "default_group": "g",
            "cluster": {"submit": str(bin_dir / "submit_server.sh"), "checkpoints": {"trained": "/ckpt"}},
            "groups": {"g": {
                "job_name": "srv", "handoff": str(self.fake / "handoff"),
                "tunnel_config": "configs/aiprover/slurm.toml",
                "prover_config": "configs/aiprover/slurm.toml", "proxy_port": 18575,
            },
            # A chat model on its own server, for roles other than the prover.
            "o": {
                "job_name": "osrv", "handoff": str(self.fake / "handoff_o"), "checkpoint": "trained",
                "server_env": {"SERVED_NAME": "chat"},
            }},
        }
        (self.fake / "site.json").write_text(json.dumps(site))
        # The workspace of the release, and the Lean project that a pack holds.
        workspace = self.directory / "orchestration_workspace"
        workspace.mkdir()
        for name in ("lakefile.lean", "lean-toolchain", "lake-manifest.json"):
            (workspace / name).write_text(name)
        self.project = self.directory / "lean_project"
        (self.project / ".lake" / "packages" / "mathlib").mkdir(parents=True)
        (self.project / ".lake" / "packages" / "mathlib" / "Mathlib.olean").write_text("olean")
        pack = ROOT / "scripts" / "slurm" / "pack.sh"
        subprocess.run(["bash", str(pack), str(self.project)], check=True, capture_output=True)
        self.environment = {
            "FAKE_SLURM": str(self.fake),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "AIPROVER_SITE": str(self.fake / "site.json"),
            "AIPROVER_RUN_EXECUTOR": "slurm",
            "AIPROVER_LEAN_PROJECT": str(self.project),
        }

    def test_a_run_job_carries_its_signal_and_keeps_its_secrets_out_of_the_script(self):
        job, mode, spec, sbatch = self.run_python(SUBMIT, **self.environment)
        self.assertEqual(job, "4242")
        self.assertEqual(mode, "0o600")
        self.assertEqual(spec["environment"], {"SECRET": "secret"})
        self.assertEqual((spec["config"], spec["group"]), ("/x.json", "g"))
        for expected in ("--job-name=aiprover_run_r1", "--signal=B:TERM@600", "--dependency=after:7", "slurm job"):
            self.assertIn(expected, sbatch)
        self.assertNotIn("secret", sbatch)

    def test_a_run_job_waits_for_its_server_and_runs_on_node_local_copies(self):
        local = self.directory / "node"
        code, outcome, base_url, solver_config, solver_toml, seen = self.run_python(
            JOB, "777|srv|RUNNING\n", **self.environment, SLURM_TMPDIR=str(local)
        )
        self.assertEqual((code, outcome["outcome"], outcome["signalled"]), (0, "exited", False))
        self.assertRegex(base_url, r"^http://127\.0\.0\.1:\d+/v1$")
        self.assertNotEqual(base_url, "http://127.0.0.1:18575/v1")
        self.assertIn(f'api_base = "{base_url}"', solver_toml)
        # The pack of the Lean project was extracted to the node's disk.
        staged = local / "lean" / "lean_project"
        self.assertIn(f'lean_project = "{staged}"', solver_toml)
        self.assertEqual((staged / ".lake/packages/mathlib/Mathlib.olean").read_text(), "olean")
        self.assertEqual(seen["AIPROVER_WORKSPACE"], str(local / "lean" / "workspace"))
        self.assertTrue((local / "lean/workspace/.lake/packages").is_symlink())
        self.assertIn(base_url, Path(seen["AIPROVER_CONFIG"]).read_text())
        # The harness compacts below the context that the server reports.
        self.assertEqual(seen["AGENT_MAX_CONTEXT"], str(int(65536 * 0.7)))
        self.assertIn("127.0.0.1", seen["NO_PROXY"])
        self.assertEqual(seen["HTTPS_PROXY"], "")  # no hosted role
        self.assertTrue(seen["AIPROVER_LIMITS_DIR"].startswith(str(local)))

    def test_a_run_job_without_a_running_server_ends_for_the_worker_to_requeue(self):
        code, outcome, base_url, *_ = self.run_python(
            JOB, "777|srv|PENDING\n", **self.environment, AIPROVER_SERVER_WAIT="0"
        )
        self.assertEqual((code, outcome["outcome"]), (75, "server_unavailable"))
        self.assertEqual(base_url, "http://127.0.0.1:18575/v1")

    def test_a_batch_run_submits_a_server_and_follows_it(self):
        last, submitted, solver = self.run_python(BATCH, "", "b1", **self.environment)
        self.assertEqual(solver, "configs/aiprover/slurm.toml")
        self.assertEqual(len(submitted), 1)
        self.assertIn("/ckpt", submitted[0])
        self.assertIn("--dependency=after:9001", last)
        # A running server is neither submitted again nor waited for.
        running = "9001|RUNNING|N/A|0:01|1:00:00|1|None|2026-01-01T00:00:00|gpu\n"
        last, submitted, _ = self.run_python(BATCH, running, "b2", **self.environment)
        self.assertEqual(len(submitted), 1)
        self.assertNotIn("--dependency", last)

    def test_a_chat_model_on_its_own_gpu_group_gets_its_own_server(self):
        chat = '"chat": {"backend": "openai_compatible", "model": "chat", "group": "o"},\n    "solver": {'
        last, submitted, _ = self.run_python(BATCH.replace('"solver": {', chat, 1), "", "b3", **self.environment)
        self.assertEqual(len(submitted), 2)
        self.assertTrue(submitted[0].startswith("/ckpt") and submitted[0].endswith(" "))
        self.assertTrue(submitted[1].endswith(" chat"))
        self.assertIn("--dependency=after:9001:9001", last)

    def test_a_batch_run_takes_its_problem_and_library_from_files(self):
        script = BATCH.replace(
            '"--problem-uuid", "OpenAIMath_164_Hindman",',
            '"--statement", "s.txt", "--library", "libraries/base.lean",',
        ).replace("print(json.dumps([", 'print(json.dumps([json.loads(slurm.spec_path(run_id).read_text())["arguments"],')
        arguments, *_ = self.run_python(script, "", "b4", **self.environment)
        self.assertIn(f"--statement={ROOT / 's.txt'}", arguments)
        self.assertEqual(arguments[arguments.index("--library") + 1], "libraries/base.lean")
        self.assertNotIn("--problem-uuid", arguments)

    def test_a_run_job_addresses_a_chat_server_directly_and_may_skip_the_egress_session(self):
        fake = self.fake
        job = JOB.replace(
            '"solver": {',
            '"chat": {"backend": "openai_compatible", "model": "chat", "group": "o"},\n'
            '    "hosted": {"backend": "claude", "model": "m"},\n    "solver": {',
            1,
        ).replace(
            '(fake / "queue")', '(fake / "handoff_o").write_text(f"node=127.0.0.1 port={server.server_port} job=778\\n")\n(fake / "queue")', 1
        ).replace("else None,\n]))", 'else None,\n    agents["chat"]["base_url"],\n]))')
        code, outcome, base_url, *_, seen, chat_url = self.run_python(
            job, "777|srv|RUNNING\n778|osrv|RUNNING\n", **self.environment,
            SLURM_TMPDIR=str(self.directory / "node"), AIPROVER_EGRESS="direct",
        )
        self.assertEqual((code, outcome["outcome"]), (0, "exited"))
        self.assertRegex(chat_url, r"^http://127\.0\.0\.1:\d+/v1$")
        self.assertNotEqual(chat_url, base_url)  # the prover's server is behind a reasoning proxy
        self.assertEqual(seen["HTTPS_PROXY"], "")

    def test_a_model_directory_alone_defines_a_cluster_with_one_server_group(self):
        default, local, checkpoints, job_name, checkpoint = self.run_python(
            DEFAULT_SITE, AIPROVER_SITE=str(self.directory / "none.json"), AIPROVER_MODEL="/weights"
        )
        self.assertEqual((default, local, checkpoints), ("slurm", True, {"trained": "/weights"}))
        self.assertEqual((job_name, checkpoint), ("aiprover_server", "trained"))

    def test_the_hosts_of_hosted_models_come_from_the_roles(self):
        hosts = self.run_python(HOSTS, **self.environment)
        self.assertEqual(
            hosts,
            ["*.anthropic.com", "*.chatgpt.com", "*.claude.ai", "*.openai.com", "anthropic.com",
             "api.example.com", "chatgpt.com", "claude.ai", "openai.com"],
        )

    def test_the_egress_proxy_relays_tls_to_hosted_apis_only(self):
        mode, replies, allowed, lookalike, parsed = self.run_python(EGRESS)
        self.assertEqual(mode, "0o600")
        self.assertEqual(
            replies,
            ["HTTP/1.1 403 Forbidden", "HTTP/1.1 403 Forbidden", "HTTP/1.1 405 CONNECT only"],
        )
        self.assertTrue(allowed)
        self.assertFalse(lookalike)
        self.assertEqual(parsed, ["::1", 443])

    def test_the_worker_resumes_a_run_its_wall_time_stopped_and_finishes_the_others(self):
        started, after, cancelled, scancel = self.run_python(WORKER, **self.environment)
        self.assertEqual(started, ["4242", "4242"])
        self.assertEqual(after["w1"]["state"], "queued")
        self.assertEqual(after["w1"]["resumptions"], 1)
        self.assertIsNone(after["w1"]["slurm_job"])
        self.assertEqual((after["w2"]["state"], after["w2"]["status"]), ("finished", "proved"))
        self.assertTrue(cancelled)
        self.assertEqual(scancel, ["--signal=TERM --batch 4243"])
