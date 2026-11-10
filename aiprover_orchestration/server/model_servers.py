"""The model servers of AIProver runs, by GPU group.

Every deployment has a persistent group, whose model server runs on its own
(by default the one that ./setup connects), and the group `cpu`, without a
model server. A deployment with a Slurm cluster (`cluster` and `groups` in
configs/site.json) adds groups with server jobs of their own: each a job
name, a handoff file on the cluster, an SSH tunnel to a local port, and a
reasoning proxy in front of it. The query server submits a group's server job
when an approved run of the group needs the model and none is queued or
running, and cancels the jobs it submitted once no run needs them.
"""

import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import tomllib
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..paths import CONFIG_DIR, ROOT, SITE, STATE_DIR

AIPROVER_CONFIGS = CONFIG_DIR / "aiprover"
# The configuration ./setup writes for this device: the harness, which talks
# to the local reasoning proxy, and the connection naming that proxy's port.
DEVICE_CONFIG = ROOT / "orchestration_plugin" / "aiprover.toml"
DEVICE_ENDPOINT = ROOT / "orchestration_plugin" / "endpoint.toml"
# The Slurm cluster whose jobs serve the model, reached over an SSH
# ControlMaster: its host, the socket, the command that submits a server job
# (given checkpoint, partition, nodes, wall time; HANDOFF and SBATCH_JOB_NAME
# exported), the job settings, and the checkpoints a run may select by name.
CLUSTER = SITE.get("cluster", {})
CLUSTER_HOST = CLUSTER.get("host", "")
CONTROL_SOCKET = Path(CLUSTER.get("control_socket", "~/.ssh/cluster.sock")).expanduser()
SUBMIT = CLUSTER.get("submit", "")
PARTITION = CLUSTER.get("partition", "")
NODES = str(CLUSTER.get("nodes", 1))
WALL_TIME = CLUSTER.get("wall_time", "02:00:00")
CHECKPOINTS: dict[str, str] = CLUSTER.get("checkpoints", {})
# The AIProver config whose ssh mode opens the cluster's tunnels.
CLUSTER_CONFIG = ROOT / CLUSTER.get("tunnel_config", "configs/aiprover/cluster.toml")


@dataclass(frozen=True)
class GpuGroup:
    """A group of runs sharing a model server. A group without a Slurm job
    name uses a persistent server, which is never submitted; a group without
    a prover config has no model server (CPU only)."""

    name: str
    # Slurm job name of the group's server jobs.
    job_name: str = ""
    # File on the cluster where the server job writes its node and port.
    handoff: str = ""
    # ssh-mode AIProver config: opens and probes the tunnel.
    tunnel_config: Path | None = None
    # AIProver config of the group's runs (through the proxy).
    prover_config: str = ""
    # Reasoning proxy (server/reasoning_proxy.py).
    proxy_port: int = 0
    # Groups whose model servers this group's runs also use: its reasoning
    # proxy pools their tunnels with its own (`--upstream`).
    borrows: tuple[str, ...] = ()
    # Key of the group's server in the site's `hardware` table.
    hardware: str = ""

    @property
    def serverless(self) -> bool:
        return not self.prover_config

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.proxy_port}/v1"


# The persistent model server: its group's name, and optionally its harness
# config, reasoning proxy port, label, hardware, and (for per-user servers)
# upstream address, model, and sessions.
PERSISTENT = SITE.get("persistent", {})
PERSISTENT_GROUP = PERSISTENT.get("name", "local")
# The reasoning proxy port that ./setup uses unless endpoint.toml names one.
SETUP_PROXY_PORT = 18575


def persistent_configs() -> tuple[Path, str, int]:
    """Harness configuration (path, repository-relative name) and reasoning
    proxy port of the persistent server: a per-user server's own
    (AIPROVER_PERSISTENT_CONFIG and AIPROVER_PERSISTENT_PROXY_PORT, set by
    scripts/user/aiprover-server), else the site's, else the one ./setup
    writes for this checkout."""
    if os.environ.get("AIPROVER_PERSISTENT_CONFIG"):
        config = Path(os.environ["AIPROVER_PERSISTENT_CONFIG"])
        return config, str(config), int(os.environ["AIPROVER_PERSISTENT_PROXY_PORT"])
    if PERSISTENT.get("config"):
        return ROOT / PERSISTENT["config"], PERSISTENT["config"], PERSISTENT["proxy_port"]
    proxy_port = SETUP_PROXY_PORT
    if DEVICE_ENDPOINT.exists():
        with open(DEVICE_ENDPOINT, "rb") as config_file:
            proxy_port = tomllib.load(config_file)["proxy"]["listen"]
    return DEVICE_CONFIG, "orchestration_plugin/aiprover.toml", proxy_port


PERSISTENT_CONFIG, PERSISTENT_PROVER_CONFIG, PERSISTENT_PROXY_PORT = persistent_configs()

CUSTOM_DIR = STATE_DIR / "model_servers"
SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
PROXY_REPLY_TOKENS = 16384


def custom_servers() -> dict[str, dict]:
    """Servers added on the page, by name: {"url", "model", "kind", "port"}.
    Kind "aiprover" runs the AIProver harness on the server, behind a
    reasoning proxy on `port`; kind "general" is any chat model."""
    return {
        path.stem: json.loads(path.read_text())
        for path in sorted(CUSTOM_DIR.glob("*.json"))
    }


def key_file(name: str) -> Path:
    return CUSTOM_DIR / f"{name}.key"


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(text)


def add_custom_server(name: str, server: dict, api_key: str) -> None:
    """Record a server, with its key apart from what the page reads."""
    CUSTOM_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    write_private(key_file(name), api_key)
    write_private(
        CUSTOM_DIR / f"{name}.json", json.dumps(server | {"port": port})
    )


def remove_custom_server(name: str) -> None:
    for suffix in (".json", ".key", ".toml"):
        (CUSTOM_DIR / f"{name}{suffix}").unlink(missing_ok=True)


def server_endpoint(name: str, server: dict) -> str:
    """The OpenAI-compatible address of a server; AIProver answers through
    a reasoning proxy, started here if it is not running."""
    if server["kind"] != "aiprover":
        return server["url"]
    port = server["port"]
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)):
            subprocess.Popen(
                [sys.executable, "-m",
                 "aiprover_orchestration.server.reasoning_proxy",
                 "--listen", str(port),
                 "--upstream", server["url"].removesuffix("/v1"),
                 "--model", server["model"],
                 "--max-reply-tokens", str(PROXY_REPLY_TOKENS)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                start_new_session=True,
            )
    return f"http://127.0.0.1:{port}/v1"


def custom_harness_config(name: str, server: dict) -> str:
    """An AIProver harness configuration for a server: that of the
    persistent server on the server's address, key, and model."""
    values = {
        "api_base": server_endpoint(name, server),
        "api_key": key_file(name).read_text() or "EMPTY",
        "model": server["model"],
    }
    text = PERSISTENT_CONFIG.read_text()
    for field, value in values.items():
        text = re.sub(
            rf"^{field} = .*$", f"{field} = {json.dumps(value)}", text,
            count=1, flags=re.M,
        )
    path = CUSTOM_DIR / f"{name}.toml"
    write_private(path, text)
    return str(path)


def site_group(name: str, spec: dict) -> GpuGroup:
    """A group with server jobs on the cluster, from the site's `groups`."""
    return GpuGroup(
        name,
        spec["job_name"],
        spec["handoff"],
        ROOT / spec["tunnel_config"],
        spec["prover_config"],
        spec["proxy_port"],
        tuple(spec.get("borrows", ())),
        spec.get("hardware", ""),
    )


GPU_GROUPS = {name: site_group(name, spec) for name, spec in SITE.get("groups", {}).items()}
GPU_GROUPS[PERSISTENT_GROUP] = GpuGroup(
    PERSISTENT_GROUP,
    tunnel_config=PERSISTENT_CONFIG,
    prover_config=PERSISTENT_PROVER_CONFIG,
    proxy_port=PERSISTENT_PROXY_PORT,
    hardware=PERSISTENT.get("hardware", PERSISTENT_GROUP),
)
# Runs whose roles all use hosted models: no model server.
GPU_GROUPS["cpu"] = GpuGroup("cpu")
DEFAULT_GROUP = SITE.get("default_group", PERSISTENT_GROUP)


def config_name(config: str) -> str:
    """The name of an AIProver config, shared by its earlier spellings: both
    aiprover/aiprover_x_logged.toml and configs/aiprover/x.toml give `x`."""
    return Path(config).stem.removeprefix("aiprover_").replace("_logged", "")


def group_of_prover(prover_config: str) -> GpuGroup | None:
    """The GPU group whose runs use this AIProver config."""
    name = config_name(prover_config)
    return next(
        (
            group
            for group in GPU_GROUPS.values()
            if group.prover_config and config_name(group.prover_config) == name
        ),
        None,
    )


logger = logging.getLogger(__name__)


def server_pool(group: GpuGroup) -> list[GpuGroup]:
    """The groups whose model servers the runs of `group` use."""
    return [group] + [GPU_GROUPS[name] for name in group.borrows]


def sharing_key(group: GpuGroup) -> str:
    """Key of the servers that the runs of `group` compete for: groups that
    borrow from each other share it, and other groups do not, even on the
    same hardware."""
    return min(member.name for member in server_pool(group))


def run_command(
    arguments: list, timeout: float, aiprover_config: Path = CLUSTER_CONFIG
) -> tuple[int, str]:
    """Run a short command; return (exit code, combined output)."""
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "AIPROVER_CONFIG": str(aiprover_config)},
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return result.returncode, (result.stdout + result.stderr).strip()


def control_master_running() -> bool:
    if not CLUSTER_HOST:
        return False
    code, _ = run_command(
        ["ssh", "-S", str(CONTROL_SOCKET), "-O", "check", CLUSTER_HOST],
        timeout=10,
    )
    return code == 0


def remote(command: str, timeout: float = 60) -> tuple[int, str]:
    """Run `command` in a login shell on the cluster through the ControlMaster."""
    return run_command(
        [
            "ssh",
            "-S",
            str(CONTROL_SOCKET),
            "-o",
            "BatchMode=yes",
            CLUSTER_HOST,
            f"bash -lc {shlex.quote(command)}",
        ],
        timeout,
    )


# Fields of a server job as `squeue -o` reports them (separated by "|"):
# id, state, start (actual if running, Slurm's estimate if pending, "N/A"
# without one), elapsed, time limit, nodes, pending reason, submission time,
# partition.
SQUEUE_FORMAT = "%i|%T|%S|%M|%l|%D|%r|%V|%P"
SQUEUE_FIELDS = (
    "id",
    "state",
    "start",
    "elapsed",
    "time_limit",
    "nodes",
    "reason",
    "submitted",
    "partition",
)


def server_job_details(group: GpuGroup) -> list[dict] | None:
    """The group's model server jobs as squeue reports them; None if the
    cluster is unreachable. `start_epoch` is the (estimated) start in seconds since
    the epoch, or None when Slurm gives no estimate."""
    if not group.job_name:
        return []
    code, output = remote(
        f'squeue -u "$USER" -n {group.job_name} -h -o "{SQUEUE_FORMAT}"'
    )
    if code != 0:
        return None
    jobs = []
    for line in output.splitlines():
        fields = line.split("|")
        if len(fields) == len(SQUEUE_FIELDS) and fields[0].isdigit():
            job = dict(zip(SQUEUE_FIELDS, fields))
            job["start_epoch"] = slurm_epoch(job["start"])
            jobs.append(job)
    return jobs


def slurm_epoch(timestamp: str) -> float | None:
    """Seconds since the epoch of a Slurm timestamp, read in this machine's
    time zone, which the cluster is taken to share; None for "N/A" and
    similar."""
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


def server_jobs(group: GpuGroup) -> dict[str, str] | None:
    """Job id → state of the group's model server jobs; None if the cluster
    is unreachable."""
    details = server_job_details(group)
    if details is None:
        return None
    return {job["id"]: job["state"] for job in details}


def handoff(group: GpuGroup) -> dict | None:
    """Job id, node and port named in the group's handoff file; None if the
    file is missing or the cluster is unreachable."""
    if not group.handoff:
        return None
    code, output = remote(f"cat {group.handoff}")
    match = re.search(r"node=(\S+) port=(\d+) job=(\d+)", output)
    if code != 0 or not match:
        return None
    return dict(zip(("node", "port", "job"), match.groups()))


def server_answers(node: str, port: str) -> bool:
    """True if the model server at node:port answers (from the login node)."""
    code, output = remote(
        f"curl -s -m 10 http://{node}:{port}/v1/models", timeout=30
    )
    return code == 0 and '"data"' in output


def submit_server(
    checkpoint: str,
    group: GpuGroup,
    partition: str = PARTITION,
    wall_time: str = WALL_TIME,
) -> str | None:
    """Submit a model server job of `group` for `checkpoint`; return its id,
    or None on failure. The job writes the group's handoff file (HANDOFF,
    exported to the job) and carries its name (SBATCH_JOB_NAME)."""
    code, output = remote(
        f"export HANDOFF={group.handoff} SBATCH_JOB_NAME={group.job_name}; "
        f"{SUBMIT} {checkpoint} {partition} {NODES} {wall_time}",
        timeout=120,
    )
    match = re.search(r"Submitted batch job (\d+)", output)
    if code != 0 or not match:
        logger.error(f"server job submission failed: {output[-500:]}")
        return None
    logger.info(
        f"submitted server job {match.group(1)} ({group.name}) for "
        f"{checkpoint} ({partition}, {NODES} nodes, {wall_time})"
    )
    return match.group(1)


def cancel_server(job_id: str) -> bool:
    caller = traceback.extract_stack(limit=3)[0]
    logger.info(f"scancel {job_id} (from {caller.name}:{caller.lineno})")
    code, output = remote(f"scancel {job_id}")
    if code != 0:
        logger.error(f"scancel {job_id} failed: {output[-300:]}")
    return code == 0
