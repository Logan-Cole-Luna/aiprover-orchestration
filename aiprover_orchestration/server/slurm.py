"""Orchestration runs as Slurm jobs.

A run executes in a CPU job of its own, so that its Lean checks and AIProver
sessions use a compute node. The query server's worker (server/jobs.py) submits
its runs this way when AIPROVER_RUN_EXECUTOR is `slurm`, and the same job serves
batch runs from the command line:

    python -m aiprover_orchestration.server.slurm submit --problem-uuid UUID
    python -m aiprover_orchestration.server.slurm cancel RUN_ID

Inside the job, `slurm job SPEC` prepares the node and runs the orchestrator:

1. It waits for the model server of each GPU group that the run uses, starts a
   reasoning proxy for the AIProver group on a free port, because jobs share
   nodes, and points the run's configs at the proxy or, for a chat model, at
   the server.
2. It extracts the Lean project to node-local disk (scripts/slurm/pack.sh), so
   that Lean loads Mathlib from local disk, and builds the run's modules there.
3. For hosted models, it opens an SSH session to the submitting login node,
   which relays TLS to the hosts that the roles call (server/egress_proxy.py),
   because compute nodes reach no external host.
4. It runs the orchestrator and passes SIGTERM on, so that a cancellation, or
   the warning that Slurm sends before the wall time, is recorded in the trace.
   The outcome of the job is written to temp/<run_id>.slurm.json.

Settings (environment); the account and partition are those sbatch reads:
  AIPROVER_RUN_EXECUTOR    local (default) or slurm
  AIPROVER_SLURM_RUN_ARGS  sbatch options of run jobs [RUN_ARGS]
  AIPROVER_SERVER_WAIT     seconds a run job waits for its model server [3600]
  AIPROVER_EGRESS          ssh (default) or direct, for nodes with internet access
  AIPROVER_PACK_ROOT       where packs lie, if not beside their directories
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .. import paths
from ..paths import DATA_DIR, RESULTS_DIR, ROOT, TEMP_DIR
from . import model_servers
from .model_servers import run_command

JOB_PREFIX = "aiprover_run_"
RUN_ARGS = "--cpus-per-task=16 --mem=64G --time=24:00:00"
# Slurm signals a run this many seconds before its wall time, which leaves the
# run time to unwind and record its state.
WARNING_SECONDS = 600
# Job ends after which the worker resumes a run in a new job, as it does after
# the loss of the model server.
INFRASTRUCTURE_STATES = {"TIMEOUT", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE"}
INFRASTRUCTURE_OUTCOMES = {"server_unavailable", "egress_unavailable"}
# Hosts that the hosted backends call, which the egress proxy allows.
BACKEND_HOSTS = {
    "claude": ("anthropic.com", "*.anthropic.com", "claude.ai", "*.claude.ai"),
    "codex": ("openai.com", "*.openai.com", "chatgpt.com", "*.chatgpt.com"),
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# The harness compacts a session's history at this share of the server's
# context; the rest holds the summary request, which carries the whole history,
# and its reply.
COMPACT_SHARE = 0.7
QUEUE_SECONDS = 30

_queue: list = [0.0, None]


def enabled() -> bool:
    return os.environ.get("AIPROVER_RUN_EXECUTOR", "local") == "slurm"


def spec_path(run_id: str) -> Path:
    return TEMP_DIR / f"{run_id}.slurm_spec.json"


def outcome_path(run_id: str) -> Path:
    return TEMP_DIR / f"{run_id}.slurm.json"


def queue(max_age: float = QUEUE_SECONDS) -> list[tuple[str, ...]] | None:
    """The (job id, name, state) of this user's jobs as squeue lists them, or
    None if squeue fails. An answer younger than `max_age` seconds is reused."""
    if _queue[1] is not None and time.time() - _queue[0] < max_age:
        return _queue[1]
    code, output = run_command(["squeue", "--me", "-h", "-o", "%i|%j|%T"], 60)
    if code != 0:
        return None
    rows = [tuple(part.strip() for part in line.split("|")) for line in output.splitlines()]
    _queue[:] = [time.time(), [row for row in rows if len(row) == 3 and row[0].isdigit()]]
    return _queue[1]


def job_states(max_age: float = QUEUE_SECONDS) -> dict[str, str] | None:
    rows = queue(max_age)
    return None if rows is None else {job_id: state for job_id, _, state in rows}


def run_jobs() -> dict[str, str] | None:
    """The job id of each queued or running run job, by run id."""
    rows = queue(0)
    return None if rows is None else {
        name.removeprefix(JOB_PREFIX): job_id
        for job_id, name, _ in rows
        if name.startswith(JOB_PREFIX)
    }


def end_state(job_id: str) -> str | None:
    """The accounting state of an ended job, such as COMPLETED or TIMEOUT."""
    code, output = run_command(["sacct", "-j", job_id, "-X", "-n", "-P", "-o", "State"], 60)
    return output.split()[0] if code == 0 and output else None


def cancel(job_id: str) -> bool:
    """Stop a run job. A job that has started gets SIGTERM through its driver,
    so that the run records the interruption; a pending job is removed."""
    pending = (job_states(max_age=0) or {}).get(job_id) == "PENDING"
    flags = [] if pending else ["--signal=TERM", "--batch"]
    return run_command(["scancel", *flags, job_id], 60)[0] == 0


def outcome(run_id: str) -> dict:
    try:
        return json.loads(outcome_path(run_id).read_text())
    except (OSError, ValueError):
        return {}


def infrastructure_end(run_id: str, job_id: str | None) -> bool:
    """True if the cluster, not the run, ended the run's job: Slurm's warning
    before the wall time, a node failure, or a model server or egress that never
    answered. The worker asks only for runs it did not cancel, so a run that was
    signalled was stopped by the cluster."""
    result = outcome(run_id)
    return (
        result.get("outcome") in INFRASTRUCTURE_OUTCOMES
        or bool(result.get("signalled"))
        or bool(job_id) and end_state(job_id) in INFRASTRUCTURE_STATES
    )


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
        file.write(text)


def submit_run(
    run_id: str,
    arguments: list[str],
    environment: dict[str, str],
    output: Path,
    group: str = "",
    dependency: str = "",
) -> str:
    """Submit the job of a run and return its id. `arguments` is the command
    line of the orchestrator, `environment` the variables it needs beyond the
    submitter's, and `group` the GPU group whose model server it uses. Both go
    to a private file, since they may hold credentials. Raises RuntimeError if
    sbatch fails."""
    config = arguments[arguments.index("--config") + 1] if "--config" in arguments else ""
    spec = {
        "run_id": run_id,
        "arguments": arguments,
        "environment": environment,
        "config": config,
        "group": group,
    }
    write_private(spec_path(run_id), json.dumps(spec, indent=1))
    outcome_path(run_id).unlink(missing_ok=True)
    # exec: the driver is the batch step, which Slurm's --signal reaches.
    driver = (
        f"cd {shlex.quote(str(ROOT))} && PYTHONPATH={shlex.quote(str(ROOT))} "
        f"exec {shlex.quote(sys.executable)} -m aiprover_orchestration.server.slurm "
        f"job {shlex.quote(str(spec_path(run_id)))}"
    )
    command = [
        "sbatch", "--parsable", f"--job-name={JOB_PREFIX}{run_id}",
        f"--output={output}", "--open-mode=append",
        f"--signal=B:TERM@{WARNING_SECONDS}",
        *shlex.split(os.environ.get("AIPROVER_SLURM_RUN_ARGS", RUN_ARGS)),
    ]
    if dependency:
        command.append(f"--dependency={dependency}")
    code, reply = run_command([*command, "--wrap", driver], timeout=120)
    # --parsable prints `id[;cluster]`, and sites add notes on other lines.
    job_id = next((line.split(";")[0] for line in reply.splitlines() if line.split(";")[0].isdigit()), "")
    if code != 0 or not job_id:
        raise RuntimeError(f"sbatch failed: {reply[-500:]}")
    return job_id


# Inside the job ----------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def served_model(group: model_servers.GpuGroup) -> str:
    """The model name that the group's server serves, from its harness config."""
    try:
        return tomllib.loads(Path(group.tunnel_config).read_text())["endpoint"]["model"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return ""


def served_context(named: dict) -> int | None:
    """The context length that the model server reports, if any."""
    try:
        with urllib.request.urlopen(f"http://{named['node']}:{named['port']}/v1/models", timeout=10) as reply:
            return min(item["max_model_len"] for item in json.load(reply)["data"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def wait_for_server(group: model_servers.GpuGroup, wait: float) -> dict | None:
    """The handoff of the group's server job once the server answers; None if
    it does not within `wait` seconds."""
    deadline = time.time() + wait
    while True:
        named = model_servers.handoff(group)
        running = (job_states(max_age=0) or {}).get(named["job"]) == "RUNNING" if named else False
        if running and model_servers.server_answers(named["node"], named["port"]):
            return named
        if time.time() >= deadline:
            return None
        time.sleep(15)


def start_proxy(named: dict, model: str, environment: dict, log) -> tuple[subprocess.Popen, str]:
    """Start a reasoning proxy for the model server; the process and the
    endpoint of the proxy, which is empty if it does not answer."""
    port = free_port()
    command = [
        sys.executable, "-m", "aiprover_orchestration.server.reasoning_proxy",
        "--listen", str(port), "--upstream", f"http://{named['node']}:{named['port']}",
        *(["--model", model] if model else []),
    ]
    proxy = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
    for _ in range(60):
        if model_servers.server_answers("127.0.0.1", str(port)):
            return proxy, f"http://127.0.0.1:{port}/v1"
        time.sleep(2)
    return proxy, ""


def set_key(text: str, key: str, value: str) -> str:
    """The TOML `text` with the string value of its first `key = ...` line set."""
    return re.sub(rf"^{key} = .*$", lambda _: f"{key} = {json.dumps(value)}", text, count=1, flags=re.M)


def harness_config(path: str, run_id: str, endpoint: str, project: Path | None) -> str:
    """A copy of a harness config, which may be a shared file, in temp/ with the
    endpoint of the run's proxy and, if staged, the Lean project of the node."""
    source = Path(path) if Path(path).is_absolute() else ROOT / path
    text = set_key(source.read_text(), "api_base", endpoint)
    if project:
        text = set_key(text, "lean_project", str(project))
    target = TEMP_DIR / f"{run_id}.{source.stem}.aiprover.toml"
    target.write_text(text)
    return str(target)


def lean_project_of(config: str) -> Path | None:
    """The Lean project that a harness config names."""
    try:
        value = tomllib.loads((ROOT / config).read_text())["paths"]["lean_project"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None
    return Path(os.path.expandvars(value)).expanduser().resolve()


def stage(project: Path, log) -> Path | None:
    """Extract the pack of a Lean project to node-local disk; the copy, or None
    without a pack or a job directory, when the harness reads the project in
    place."""
    pack = Path(os.environ.get("AIPROVER_PACK_ROOT") or project.parent) / f"{project.name}.pack"
    local = os.environ.get("SLURM_TMPDIR")
    if not local or not (pack / "all.list").is_file():
        return None
    started = time.time()
    shell = ["bash", str(ROOT / "scripts" / "slurm" / "pack.sh"), "-x", str(pack), f"{local}/lean"]
    done = subprocess.run(shell, stdout=log, stderr=log)
    staged = Path(local) / "lean" / project.name
    if done.returncode or not (staged / ".lake" / "packages").is_dir():
        print(f"[slurm] cannot stage {project}; the harness reads it in place", flush=True)
        return None
    print(f"[slurm] staged {project} at {staged} in {time.time() - started:.0f}s", flush=True)
    return staged


def local_workspace(staged: Path, release: str) -> Path:
    """A Lake workspace for the run's modules beside the staged project, linked
    to its packages, so that Lean loads Mathlib from node-local disk."""
    template = paths.workspace_of(release or paths.DEFAULT_LEAN_RELEASE)
    workspace = staged.parent / "workspace"
    for folder in ("Definitions", "Theorems", "Solutions", ".lake"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    for name in ("lakefile.lean", "lean-toolchain", "lake-manifest.json"):
        shutil.copyfile(template / name, workspace / name)
    (workspace / ".lake" / "packages").symlink_to(staged / ".lake" / "packages")
    return workspace


def hosted_hosts(agents: list[dict]) -> list[str]:
    """Host patterns of the model APIs that the agents call from the cluster:
    those of their backends, and the host of any other address that is not
    this machine or served by a GPU group."""
    hosts = []
    for agent in agents:
        for spec in [agent, *(agent.get("phases") or {}).values()]:
            backend = spec.get("backend", agent.get("backend"))
            hosts += BACKEND_HOSTS.get(backend, ())
            host = urlsplit(spec.get("base_url") or "").hostname
            if backend == "openai_compatible" and host and not spec.get("group") and host not in LOCAL_HOSTS:
                hosts.append(host)
    return sorted(set(hosts))


def open_egress(host: str, allow: list[str], log) -> tuple[subprocess.Popen, int] | None:
    """Start the egress proxy on `host` through SSH, allowing the host patterns
    `allow`, and forward a local port to it; None if the port does not relay a
    CONNECT within a minute."""
    port = free_port()
    socket_path = f"/tmp/aiprover-egress-{os.getuid()}/{os.environ.get('SLURM_JOB_ID', os.getpid())}.sock"
    remote = (
        f"cd {shlex.quote(str(ROOT))} && PYTHONPATH={shlex.quote(str(ROOT))} "
        f"exec {shlex.quote(sys.executable)} -m aiprover_orchestration.server.egress_proxy "
        f"--unix {socket_path} --exit-on-stdin-eof {' '.join('--allow ' + shlex.quote(h) for h in allow)}"
    )
    options = ["BatchMode=yes", "ExitOnForwardFailure=yes", "ServerAliveInterval=30", "StrictHostKeyChecking=accept-new"]
    # The proxy exits when the session ends and closes its stdin.
    session = subprocess.Popen(
        ["ssh", "-q", *[a for o in options for a in ("-o", o)], "-L", f"127.0.0.1:{port}:{socket_path}", host, remote],
        stdin=subprocess.PIPE, stdout=log, stderr=log,
    )
    deadline = time.time() + 60
    while time.time() < deadline and session.poll() is None:
        if relays(port, allow[0].removeprefix("*.")):
            return session, port
        time.sleep(2)
    session.terminate()
    return None


def relays(port: int, host: str) -> bool:
    """True if the forwarded port answers a CONNECT to `host`."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as probe:
            probe.sendall(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode())
            return probe.recv(32).startswith(b"HTTP/1.1 200")
    except OSError:
        return False


def run_groups(agents: list[dict], group: model_servers.GpuGroup) -> list[model_servers.GpuGroup]:
    """The GPU groups whose model servers the agents use: `group` for the
    AIProver agents and those at its endpoint, and each group that an agent
    names with `group`."""
    used = []
    for agent in agents:
        if agent.get("group"):
            target = model_servers.GPU_GROUPS[agent["group"]]
        elif agent.get("backend") == "aiprover" or agent.get("base_url") == group.endpoint:
            target = group
        else:
            continue
        if (target.prover_config or target.job_name) and target not in used:
            used.append(target)
    return used


def connect(group: model_servers.GpuGroup, environment: dict, helpers: list, log) -> tuple[dict, str] | None:
    """The handoff and the endpoint that the run uses for the group's server,
    or None if the server or its reasoning proxy does not answer. A group with
    a harness config is reached through a reasoning proxy of the job; any other
    group is a chat model and is addressed directly."""
    named = wait_for_server(group, float(os.environ.get("AIPROVER_SERVER_WAIT", "3600")))
    if named is None:
        return None
    if not group.prover_config:
        return named, f"http://{named['node']}:{named['port']}/v1"
    proxy, endpoint = start_proxy(named, served_model(group), environment, log)
    helpers.append(proxy)
    return (named, endpoint) if endpoint else None


def prepare_prover(agents: list[dict], run_id: str, endpoint: str, context: int | None, environment: dict) -> None:
    """Point the AIProver harness configs at the endpoint, stage the Lean
    project on the node, and set the harness's compaction threshold."""
    provers = [a for a in agents if a.get("backend") == "aiprover" and a.get("config")]
    configs = [agent["config"] for agent in provers]
    configs += [environment["AIPROVER_CONFIG"]] if "AIPROVER_CONFIG" in environment else []
    projects = {lean_project_of(path) for path in configs} - {None}
    staged = stage(projects.pop(), sys.stdout) if len(projects) == 1 else None
    if staged:
        workspace = local_workspace(staged, environment.get("AIPROVER_LEAN_RELEASE", ""))
        environment.setdefault("AIPROVER_WORKSPACE", str(workspace))
    for agent in provers:
        agent["config"] = harness_config(agent["config"], run_id, endpoint, staged)
    if "AIPROVER_CONFIG" in environment:
        environment["AIPROVER_CONFIG"] = harness_config(environment["AIPROVER_CONFIG"], run_id, endpoint, staged)
    if context:
        environment.setdefault("AGENT_MAX_CONTEXT", str(int(context * COMPACT_SHARE)))


def job_main(spec_file: Path) -> int:
    spec = json.loads(spec_file.read_text())
    run_id = spec["run_id"]
    group = model_servers.GPU_GROUPS.get(spec["group"])
    config_file = Path(spec["config"]) if spec["config"] else None
    config = json.loads(config_file.read_text()) if config_file else {}
    agents = list(config.get("agents", {}).values())
    environment = {
        "AIPROVER_ORCHESTRATION_ROOT": str(ROOT),
        **os.environ,
        "PYTHONPATH": str(ROOT),
        **spec["environment"],
    }
    # Machine slots count the allocation of this job only, because jobs share nodes.
    if os.environ.get("SLURM_TMPDIR"):
        environment.setdefault("AIPROVER_LIMITS_DIR", f"{os.environ['SLURM_TMPDIR']}/aiprover_limits")
    helpers: list[subprocess.Popen] = []

    def finish(result: str, code: int, **details) -> int:
        for helper in helpers:
            helper.terminate()
        for helper in helpers:
            try:
                helper.wait(timeout=10)
            except subprocess.TimeoutExpired:
                helper.kill()
        record = {"job": os.environ.get("SLURM_JOB_ID", ""), "node": socket.gethostname()}
        write_private(outcome_path(run_id), json.dumps({**record, "outcome": result, "returncode": code, **details}))
        print(f"[slurm] {run_id}: {result} (code {code})", flush=True)
        return code

    print(f"[slurm] {run_id} on {socket.gethostname()} (job {os.environ.get('SLURM_JOB_ID')})", flush=True)
    no_proxy = ["localhost", "127.0.0.1"]
    endpoints, contexts = {}, {}
    for used in run_groups(agents, group) if group else []:
        connected = connect(used, environment, helpers, sys.stdout)
        if connected is None:
            return finish("server_unavailable", 75, handoff=used.handoff)
        named, endpoints[used.name] = connected
        no_proxy.append(named["node"])
        print(f"[slurm] model server job {named['job']} at {named['node']}:{named['port']} -> {endpoints[used.name]}", flush=True)
        contexts[used.name] = served_context(named)
        if used == group and used.prover_config:
            prepare_prover(agents, run_id, endpoints[used.name], contexts[used.name], environment)
    for agent in agents:
        served = agent.get("group") or (group.name if group and agent.get("base_url") == group.endpoint else "")
        if served in endpoints:
            agent["base_url"] = endpoints[served]
            if contexts[served]:
                agent.setdefault("max_context", contexts[served])
    arguments = spec["arguments"]
    if config_file:
        # The submitted config stays as it is, so that a resubmission starts from it.
        job_config = config_file.with_name(f"{config_file.stem}.job.json")
        job_config.write_text(json.dumps(config, indent=1))
        arguments = [str(job_config) if argument == str(config_file) else argument for argument in arguments]

    allow = hosted_hosts(agents)
    if allow and os.environ.get("AIPROVER_EGRESS", "ssh") == "ssh":
        host = os.environ.get("SLURM_SUBMIT_HOST", "")
        opened = open_egress(host, allow, sys.stdout) if host else None
        if opened is None:
            return finish("egress_unavailable", 75, host=host)
        helpers.append(opened[0])
        for name in ("HTTPS_PROXY", "https_proxy"):
            environment[name] = f"http://127.0.0.1:{opened[1]}"
        print(f"[slurm] hosted models through {host}: {', '.join(allow)}", flush=True)
    for name in ("NO_PROXY", "no_proxy"):
        environment[name] = ",".join([*no_proxy, environment.get(name, "")]).strip(",")

    child = subprocess.Popen(arguments, cwd=ROOT, env=environment)
    signalled = []

    def forward(signum, _frame):
        signalled.append(signum)
        child.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    return finish("exited", child.wait(), signalled=bool(signalled))


# Batch commands ----------------------------------------------------------------


def resumable(run_id: str) -> None:
    """Exit unless the run has a trace to continue and is not finished."""
    if not (RESULTS_DIR / run_id / "trace.json").exists():
        raise SystemExit(f"no trace of run {run_id} to resume")
    try:
        status = json.loads((RESULTS_DIR / run_id / "summary.json").read_text()).get("status")
    except (OSError, ValueError):
        return
    if status in ("proved", "disproved"):
        raise SystemExit(f"run {run_id} is already {status}")


def submit(args) -> None:
    from ..lean import releases
    from ..orchestrator import libraries
    from . import jobs

    suffix = args.problem_uuid.rsplit("_", 1)[-1] if args.problem_uuid else "files"
    run_id = args.resume or args.run_id or f"{time.strftime('%Y%m%d_%H%M%S')}_{suffix}"
    previous = {}
    if args.resume:
        resumable(run_id)
        # A resumed run keeps the settings of its last submission unless given anew.
        try:
            previous = json.loads(spec_path(run_id).read_text())
        except (OSError, ValueError):
            pass
        before = previous.get("arguments", [])
        args.lean_release = args.lean_release or previous.get("environment", {}).get("AIPROVER_LEAN_RELEASE", "")
        if not args.library and "--library" in before:
            args.library = before[before.index("--library") + 1]
    elif not (args.problem_uuid or args.statement):
        raise SystemExit("--problem-uuid or --statement is needed for a new run")
    group = model_servers.GPU_GROUPS[args.group or previous.get("group") or model_servers.DEFAULT_GROUP]
    source = args.config or (previous.get("config") if Path(previous.get("config", "")).is_file() else "") or jobs.CONFIG
    config = jobs.group_config(json.loads(Path(source).read_text()), group)
    # The group of the run serves every prover of the run.
    for agent in config["agents"].values():
        if agent.get("backend") == "aiprover":
            agent["config"] = group.prover_config
    if run_id in (run_jobs() or {}):
        raise SystemExit(f"run {run_id} already has a job")
    config_path = TEMP_DIR / f"{run_id}.config.json"
    files = {"statement": args.statement, "proof": args.proof, "formal-statement": args.formal_statement}
    arguments = [
        sys.executable, "-m", "aiprover_orchestration.orchestrator.run",
        "--dataset", str(Path(args.dataset).resolve()), "--config", str(config_path),
        "--run-id", run_id, "--max-restarts", "0",
        *(["--resume"] if args.resume else ["--problem-uuid", args.problem_uuid] if args.problem_uuid else []),
        *[f"--{name}={Path(value).resolve()}" for name, value in files.items() if value and not args.resume],
        *(["--library", args.library] if args.library else []),
        *args.options,
    ]
    groups = run_groups(list(config["agents"].values()), group)
    environment, waiting = {}, []
    if group in groups:
        environment["AIPROVER_CONFIG"] = str(group.tunnel_config)
    for used in groups:
        servers = model_servers.server_jobs(used) or {}
        if not servers and not args.no_server:
            checkpoint = model_servers.CHECKPOINTS.get(used.checkpoint or args.checkpoint, "")
            if not (checkpoint and used.handoff):
                raise SystemExit(f"group {used.name} has no model server to submit: set AIPROVER_MODEL or the site file (docs/guide/slurm.md)")
            job_id = model_servers.submit_server(checkpoint, used)
            if not job_id:
                raise SystemExit(f"model server submission failed for group {used.name}")
            servers = {job_id: "PENDING"}
            print(f"model server job {job_id} submitted for group {used.name}")
        if servers and "RUNNING" not in servers.values():
            waiting += servers
    # The run job starts with its servers and waits there for the models.
    dependency = f"after:{':'.join(waiting)}" if waiting else ""
    if args.library and libraries.is_package(args.library):
        environment["AIPROVER_LIBRARY_PACKAGE"] = str(libraries.resolve(args.library))
    if args.lean_release:
        release = releases.release_of(args.lean_release)
        if not releases.is_installed(release):
            raise SystemExit(f"Lean {release} is not installed: bash scripts/slurm/lean_release.sh {release}")
        environment |= jobs.release_environment(
            release, group.tunnel_config if group in groups else None, config["agents"]
        )
    if "AIPROVER_LIBRARY_PACKAGE" in environment:
        jobs.library_search(environment, config["agents"], Path(args.library).name)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=1))
    output = TEMP_DIR / f"{run_id}.out"
    job_id = submit_run(run_id, arguments, environment, output, group.name, dependency)
    print(f"run {run_id}: job {job_id}{f' ({dependency})' if dependency else ''}")
    print(f"log {output}\nresults {RESULTS_DIR / run_id}")


def list_runs(limit: int) -> None:
    """Print the queued runs and then the newest finished ones: run id, state,
    and job id."""
    queued = run_jobs() or {}
    states = job_states(0) or {}
    finished = sorted(
        (path.parent for path in RESULTS_DIR.glob("*/trace.json")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run_id in [*queued, *(p.name for p in finished if p.name not in queued)][:limit]:
        if run_id in queued:
            state = states.get(queued[run_id], "queued")
        else:
            try:
                state = json.loads((RESULTS_DIR / run_id / "summary.json").read_text()).get("status", "unknown")
            except (OSError, ValueError):
                state = "stopped"
        print(f"{run_id}\t{state}\t{queued.get(run_id, '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("job", help="inside a run job: prepare the node and run").add_argument("spec", type=Path)
    batch = commands.add_parser("submit", help="submit a batch run")
    batch.add_argument("--problem-uuid", default="", help="a problem of --dataset")
    batch.add_argument("--statement", type=Path, help="informal statement (file), instead of a problem of the dataset")
    batch.add_argument("--proof", type=Path, help="informal proof (file)")
    batch.add_argument("--formal-statement", type=Path, help="Lean statement (file; mode prove)")
    batch.add_argument("--library", default="", help="library of verified results to build on (libraries.py)")
    batch.add_argument("--dataset", default=str(DATA_DIR / "openai_math.jsonl"))
    batch.add_argument("--config", help="run config [the site's served config]")
    batch.add_argument("--group", default="", help="GPU group [the site's default group]")
    batch.add_argument("--checkpoint", default="trained", help="version of cluster.checkpoints for a new server")
    batch.add_argument("--run-id", default="")
    batch.add_argument("--resume", default="", metavar="RUN_ID", help="continue a run from its trace")
    batch.add_argument("--lean-release", default="", help="an installed Lean release, such as v4.34.1, other than the pin")
    batch.add_argument("--no-server", action="store_true", help="submit no model server job")
    batch.add_argument("options", nargs="*", help="orchestrator options after --")
    listing = commands.add_parser("list", help="show the queued runs and the newest finished ones")
    listing.add_argument("-n", "--limit", type=int, default=20)
    commands.add_parser("cancel", help="stop a run job").add_argument("run_id")
    args = parser.parse_args()
    if args.command == "job":
        sys.exit(job_main(args.spec))
    if args.command == "submit":
        return submit(args)
    if args.command == "list":
        return list_runs(args.limit)
    job_id = (run_jobs() or {}).get(args.run_id)
    if not job_id:
        raise SystemExit(f"run {args.run_id} has no queued or running job")
    sys.exit(0 if cancel(job_id) else 1)


if __name__ == "__main__":
    main()
