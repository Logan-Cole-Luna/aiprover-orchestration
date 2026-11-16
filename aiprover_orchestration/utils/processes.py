"""Commands run as a process group, ended as a whole."""

import fcntl
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

# Interval at which a running command looks at its stop event.
STOP_POLL_SECONDS = 1
# Programs of the hosted backends, installed for the account the first time a
# command needs one, so that nobody installs a program they never use. Both
# installers place their program in ~/.local/bin; a program already on the
# path keeps its place.
INSTALLERS = {
    "claude": "curl -fsSL https://claude.ai/install.sh | bash",
    "codex": "npm install -g --prefix ~/.local @openai/codex@0.166.0",
}
LOCAL_BIN = Path.home() / ".local" / "bin"
if str(LOCAL_BIN) not in os.environ.get("PATH", "").split(":"):
    os.environ["PATH"] = f"{os.environ.get('PATH', '')}:{LOCAL_BIN}"


def installed(program: str) -> bool:
    """Whether `program` is on the path, installing it first if this account
    lacks it. Concurrent processes of the account install it once."""
    if shutil.which(program) or program not in INSTALLERS:
        return shutil.which(program) is not None
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_BIN.parent / ".aiprover_install.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not shutil.which(program):
            try:
                subprocess.run(INSTALLERS[program], shell=True, capture_output=True, timeout=600)
            except subprocess.TimeoutExpired:
                pass
    return shutil.which(program) is not None


class ProcessStopped(Exception):
    """A command ended by its stop event."""


def _end_group(process: subprocess.Popen, grace: float) -> None:
    """End the process group of `process`; `grace` seconds after SIGTERM
    let its processes end theirs."""
    try:
        if grace:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=grace)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_group(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    stop: threading.Event | None = None,
    stdin: str | None = None,
    env: dict | None = None,
    merge_stderr: bool = False,
    grace: float = 0,
) -> subprocess.CompletedProcess:
    """Run `command` in a process group of its own. Raises TimeoutExpired
    or ProcessStopped once the group is ended."""
    if stop is not None and stop.is_set():
        raise ProcessStopped
    installed(command[0])
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        while True:
            try:
                stdout, stderr = process.communicate(
                    stdin, timeout=STOP_POLL_SECONDS
                )
                return subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
            except subprocess.TimeoutExpired:
                # `communicate` continues with what it has left to send.
                stdin = None
            stopped = stop is not None and stop.is_set()
            if stopped or time.monotonic() >= deadline:
                _end_group(process, grace)
                if stopped:
                    raise ProcessStopped
                raise subprocess.TimeoutExpired(command, timeout)
