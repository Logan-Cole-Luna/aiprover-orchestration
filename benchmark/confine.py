"""Run the Claude CLI under Landlock, with an empty home.

    python3 -I confine.py --run DIR --credentials FILE [--execute PATH]...
        [--read PATH]... [--lean yes] -- command [argument]...

The command reads the system directories, reads and writes `DIR` and the
credentials file, executes only the listed paths and the ELF loader, and
opens TCP connections to port 443 only (Landlock does not cover UDP, which
name resolution uses). The repository, its results and
libraries, the caller's home, and the Lean toolchain are outside its reach.
The environment is replaced, so that no setting of the caller reaches it.
With `--lean`, it also executes the Lean toolchain, Git (which Lake runs), the
shell, and the filters of LEAN_FILTERS; no other program is reachable, so that
port 443 serves the model API alone.
"""

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "aiprover_orchestration" / "lean" / "sandbox.py"
# The kernel views, and the resolver configuration of systemd-resolved.
READ = ("/proc", "/sys", "/run/systemd/resolve")
API_PORT = 443
MAX_OUTPUT_TOKENS = "128000"
LEAN_SHELL = "/usr/bin/bash"
LEAN_FILTERS = ("/usr/bin/head", "/usr/bin/tail", "/usr/bin/grep")
LEAN_TIMEOUT_MS = "1800000"


def load_sandbox():
    """lean/sandbox.py, by path: `-I` leaves the package out of the path."""
    spec = importlib.util.spec_from_file_location("sandbox", SANDBOX)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lean_environment(elan: str) -> dict[str, str]:
    return {
        "PATH": f"{elan}/bin:/usr/bin:/bin",
        "ELAN_HOME": elan,
        "SHELL": LEAN_SHELL,
        "BASH_DEFAULT_TIMEOUT_MS": LEAN_TIMEOUT_MS,
        "BASH_MAX_TIMEOUT_MS": LEAN_TIMEOUT_MS,
    }


def environment(run: str) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": f"{run}/home",
        "TMPDIR": f"{run}/tmp",
        "LANG": "C.UTF-8",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": MAX_OUTPUT_TOKENS,
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


def main(arguments: list[str]) -> None:
    split = arguments.index("--")
    options, command = arguments[:split], arguments[split + 1 :]
    values: dict[str, list[str]] = {}
    for flag, value in zip(options[::2], options[1::2]):
        values.setdefault(flag, []).append(os.path.realpath(value))
    run = values["--run"][0]
    sandbox = load_sandbox()
    abi = sandbox.abi_version()
    if abi < 4:
        sys.exit(f"confine.py: Landlock ABI {abi} < 4; refusing to run")
    execute = [*sandbox.SYSTEM_EXECUTE[:2], *values.get("--execute", [])]
    extra = {}
    if "--lean" in values:
        elan = os.path.realpath(sandbox.ELAN_HOME)
        execute += [*sandbox.SYSTEM_EXECUTE[2:], elan, LEAN_SHELL, *LEAN_FILTERS]
        extra = lean_environment(elan)
    sandbox.restrict(
        read=[*sandbox.SYSTEM_READ, *READ, *values.get("--read", [])],
        write=[run, *values.get("--credentials", [])],
        execute=execute,
        abi=abi,
        connect_ports=(API_PORT,),
    )
    os.execvpe(command[0], command, {**environment(run), **extra})


if __name__ == "__main__":
    main(sys.argv[1:])
