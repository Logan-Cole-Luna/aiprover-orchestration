"""Commands run as a process group, ended as a whole."""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

# Interval at which a running command looks at its stop event.
STOP_POLL_SECONDS = 1


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
