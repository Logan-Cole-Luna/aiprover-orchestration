"""Run a command without internet access (Landlock, Linux 6.7 or later).

    python3 -I offline.py PORT[,PORT...] -- command [argument]...

The command and every process it starts, including detached workers, may
open TCP connections only to the listed ports, which name the local services
of an AIProver session: the model proxy and the Mathlib index. Fails closed:
without Landlock ABI 4 the command does not run.
"""

import importlib.util
import os
import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parent / "sandbox.py"


def main(arguments: list[str]) -> None:
    if len(arguments) < 3 or arguments[1] != "--":
        sys.exit("usage: offline.py PORT[,PORT...] -- command")
    spec = importlib.util.spec_from_file_location("sandbox", SANDBOX)
    sandbox = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sandbox)
    abi = sandbox.abi_version()
    if abi < 4:
        sys.exit(f"offline.py: Landlock ABI {abi} < 4; refusing to run")
    ports = tuple(int(port) for port in arguments[0].split(","))
    sandbox.restrict_network(ports, abi)
    os.execvp(arguments[2], arguments[2:])


if __name__ == "__main__":
    main(sys.argv[1:])
