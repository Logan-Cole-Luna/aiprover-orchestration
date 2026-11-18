"""The `singular` command of agents with tools: run a Singular script, for
Gröbner bases and ideal membership certificates. Run with `python3 -I`.

    singular SCRIPT [--seconds N]

The script runs under the sandbox of lean/sandbox.py: it writes only in the
working directory and opens no connection. The executable is SINGULAR, or
`Singular` on the search path. SINGULAR_READ, if set, names the tree it reads
(its libraries); by default the directory above its own. Every call is appended to the JSON lines file
SINGULAR_LOG, which the run sets.

Its answers direct the search and prove nothing: a certificate
Σ gᵢ fᵢ = 1 enters a proof through `linear_combination`, which Lean checks.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "lean" / "sandbox.py"
MAX_SECONDS = 1800
MAX_REPLY = 20000


def executable() -> str:
    """The Singular executable of this site, or '' if it has none."""
    return os.environ.get("SINGULAR") or shutil.which("Singular") or ""


def main(arguments: list[str]) -> str:
    parser = argparse.ArgumentParser(prog="singular")
    parser.add_argument("script", type=Path)
    parser.add_argument("--seconds", type=int, default=120)
    options = parser.parse_args(arguments)
    seconds = max(1, min(options.seconds, MAX_SECONDS))
    program = os.path.realpath(executable())
    tree = os.environ.get("SINGULAR_READ") or str(Path(program).parents[1])
    start = time.time()
    try:
        run = subprocess.run(
            [sys.executable, "-I", str(SANDBOX), "--read", tree,
             "--execute", program, "--write", str(Path.cwd()),
             "--cpu-seconds", str(seconds),
             "--", program, "-q", "--no-rc", str(options.script)],
            capture_output=True, text=True, timeout=seconds + 30,
        )  # fmt: skip
        reply = run.stdout + run.stderr
        answer = "error" if run.returncode else "done"
    except subprocess.TimeoutExpired:
        reply, answer = "", "timeout"
    elapsed = round(time.time() - start, 1)
    if log := os.environ.get("SINGULAR_LOG"):
        with open(log, "a") as records:
            records.write(json.dumps({
                "time": round(start), "script": str(options.script),
                "answer": answer, "seconds": elapsed,
            }) + "\n")  # fmt: skip
    reply = reply if len(reply) <= MAX_REPLY else reply[:MAX_REPLY] + "\n[output truncated]"
    return f"{reply}\n[{answer} in {elapsed} s]"


if __name__ == "__main__":
    print(main(sys.argv[1:]))
