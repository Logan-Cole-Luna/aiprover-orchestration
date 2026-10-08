"""AIProver model server jobs on Vista, driven through the ControlMaster.

Runs are divided into GPU groups, each with its own model server: a Slurm
job of its own name, a handoff file on Vista, an SSH tunnel to a local port
and a reasoning proxy in front of it. The query server submits a group's
server job (`scripts/submit_aiprover_vista.sh` of the checkout
`$WORK/aiprover_serve` on the login node; `scripts/vista/` here) when an
approved run of the group needs the model and none is queued or running, and
cancels the jobs it submitted once no run of the group needs them.
"""

import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..paths import CONFIG_DIR

AIPROVER_CONFIGS = CONFIG_DIR / "aiprover"
AIPROVER_CONFIG = AIPROVER_CONFIGS / "vista.toml"
CONTROL_SOCKET = Path.home() / ".ssh" / "vista.sock"
VISTA_HOST = "vista.tacc.utexas.edu"

# Server job settings; the defaults launch on gh-dev (starts in minutes,
# 2 h limit). A run that outlives its server job resumes on the next one.
PARTITION = os.environ.get("VISTA_PARTITION", "gh-dev")
NODES = os.environ.get("VISTA_NODES", "2")
WALL_TIME = os.environ.get("VISTA_WALL_TIME", "02:00:00")
# AIProver versions a run may select: the trained model (Mistral format, FP8)
# and the base model (HF format, per-expert; `$SCRATCH` expands on Vista).
CHECKPOINTS = {
    "trained": os.environ.get(
        "VISTA_CHECKPOINT", "/work/11428/pjana/aiprover_model"
    ),
    "base": os.environ.get(
        "VISTA_CHECKPOINT_BASE",
        "$SCRATCH/aiprover_ckpt/leanstral_base_unpacked",
    ),
}


@dataclass(frozen=True)
class GpuGroup:
    name: str
    job_name: str  # Slurm job name of the group's server jobs
    handoff: str  # file on Vista where the server job writes its node and port
    tunnel_config: Path  # ssh-mode AIProver config: opens and probes the tunnel
    solver_config: (
        str  # AIProver config of the group's runs (through the proxy)
    )
    proxy_port: int  # reasoning proxy (server/reasoning_proxy.py)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.proxy_port}/v1"


DEFAULT_GROUP = "main"
GPU_GROUPS = {
    # `aiprover_srv` is `#SBATCH -J` in scripts/serve_aiprover_vista.sbatch.
    "main": GpuGroup(
        "main",
        "aiprover_srv",
        "$SCRATCH/servers/aiprover_server.txt",
        AIPROVER_CONFIGS / "vista.toml",
        "configs/aiprover/vista_logged.toml",
        18565,
    ),
    "open": GpuGroup(
        "open",
        "aiprover_srv_open",
        "$SCRATCH/servers/aiprover_server_open.txt",
        AIPROVER_CONFIGS / "vista_open.toml",
        "configs/aiprover/vista_logged_open.toml",
        18568,
    ),
}

logger = logging.getLogger(__name__)


def run_command(
    arguments: list, timeout: float, aiprover_config: Path = AIPROVER_CONFIG
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
    code, _ = run_command(
        ["ssh", "-S", str(CONTROL_SOCKET), "-O", "check", VISTA_HOST],
        timeout=10,
    )
    return code == 0


def remote(command: str, timeout: float = 60) -> tuple[int, str]:
    """Run `command` in a login shell on Vista through the ControlMaster."""
    return run_command(
        [
            "ssh",
            "-S",
            str(CONTROL_SOCKET),
            "-o",
            "BatchMode=yes",
            VISTA_HOST,
            f"bash -lc {shlex.quote(command)}",
        ],
        timeout,
    )


def server_jobs(group: GpuGroup) -> dict[str, str] | None:
    """Job id → state of the group's model server jobs; None if Vista is
    unreachable."""
    code, output = remote(
        f'squeue -u "$USER" -n {group.job_name} -h -o "%i %T"'
    )
    if code != 0:
        return None
    jobs = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit():
            jobs[fields[0]] = fields[1]
    return jobs


def submit_server(checkpoint: str, group: GpuGroup) -> str | None:
    """Submit a model server job of `group` for `checkpoint`; return its id,
    or None on failure. The job writes the group's handoff file (HANDOFF,
    exported to the job) and carries its name (SBATCH_JOB_NAME)."""
    code, output = remote(
        f"cd $WORK/aiprover_serve && HANDOFF={group.handoff} "
        f"SBATCH_JOB_NAME={group.job_name} scripts/submit_aiprover_vista.sh "
        f"{checkpoint} {PARTITION} {NODES} {WALL_TIME}",
        timeout=120,
    )
    match = re.search(r"Submitted batch job (\d+)", output)
    if code != 0 or not match:
        logger.error(f"Vista submission failed: {output[-500:]}")
        return None
    logger.info(
        f"submitted Vista server job {match.group(1)} ({group.name}) for {checkpoint} "
        f"({PARTITION}, {NODES} nodes, {WALL_TIME})"
    )
    return match.group(1)


def cancel_server(job_id: str) -> bool:
    code, output = remote(f"scancel {job_id}")
    if code != 0:
        logger.error(f"scancel {job_id} failed: {output[-300:]}")
    return code == 0
