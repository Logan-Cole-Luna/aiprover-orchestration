"""Stand-in for the AIProver CLI (`bin/aiprover`): jobs that run until they
are cancelled. Its state and a log of the commands it received live in
$FAKE_AIPROVER_DIR."""

import json
import os
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_AIPROVER_DIR"])


def log(line: str) -> None:
    with open(STATE / "commands.log", "a") as commands:
        commands.write(line + "\n")


def main(command: str, *args: str) -> int:
    if command == "submit":
        job = f"job{len(list(STATE.glob('job*.submitted'))) + 1}"
        (STATE / f"{job}.submitted").touch()
        log(f"submit {job}")
        print(job)
    elif command == "wait":
        job, timeout = args[0], float(args[args.index("--timeout") + 1])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (STATE / f"{job}.cancelled").exists():
                return 0
            time.sleep(0.05)
        return 3  # still running
    elif command == "cancel":
        (STATE / f"{args[0]}.cancelled").touch()
        log(f"cancel {args[0]}")
    elif command == "result":
        print(json.dumps({"samples": [{"sample": 0, "status": "cancelled"}]}))
    elif command == "render":
        print("problem")
    elif command == "status":
        print(json.dumps({"running": 1, "queued": 0}))
    elif command == "slots":
        print(json.dumps({"free": 0}))
    elif command == "grow":
        print(0)
    elif command == "resume":
        return 1  # this CLI cannot resume
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
