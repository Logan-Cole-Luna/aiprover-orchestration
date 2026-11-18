"""The `sat-solve` command of agents with tools: explore propositional
formulas with CaDiCaL, the SAT solver shipped with the Lean toolchain.
Run with `python3 -I`.

    sat-solve FORMULA [ARGUMENT]... [--seconds N]
              [--sweep A..B | --all K | --split NAME,... | --lrat NAME]

FORMULA is a DIMACS CNF file, or a Python script that prints one. A script
receives the ARGUMENTs, so that one encoding serves several sizes or cases,
and runs under the sandbox of lean/sandbox.py: it writes only in the working
directory and opens no connection. A comment line `c var <n> <name>` names
variable n, and assignments are printed by these names.

The command covers what `bv_decide` does not: `--sweep` decides the formula
of a script for each size from A to B, appended as its last argument, to find
a threshold or the smallest counterexample of a statement about all sizes;
`--all` enumerates up to K assignments of the named variables, to count
solutions or show uniqueness; `--split` decides each case of the named
variables, to design a case split whose cases are small enough for
`bv_decide`; `--lrat` writes, for an unsatisfiable formula, the file NAME.lean
with Mathlib's `lrat_proof` command, whose certificate the kernel checks on
the standard axioms. Every answer reports its time.

The answers direct the search and prove nothing; Lean decides. Every call is
appended to the JSON lines file SAT_SEARCH_LOG, which the run sets.
"""

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiprover_orchestration.lean.sandbox import ELAN_HOME  # noqa: E402

SANDBOX = Path(__file__).resolve().parents[1] / "lean" / "sandbox.py"
MAX_SECONDS = 900
MAX_REPLY = 20_000
MAX_SWEEP = 200
MAX_ALL = 1000
MAX_SPLIT = 8
# A certificate beyond this size is stopped: Mathlib's `lrat_proof` would not
# check it in a call, and an uncapped certificate fills the disk at megabytes
# per second.
MAX_CERTIFICATE_BYTES = 200 * 2**20
# Free space below which `--lrat` does not start.
MIN_FREE_BYTES = 5 * 2**30
# CaDiCaL's exit codes.
SATISFIABLE, UNSATISFIABLE = 10, 20


def formula_text(formula: Path, arguments: list[str], seconds: int) -> str:
    """The DIMACS text of a file, or the output of a script."""
    if formula.suffix != ".py":
        return formula.read_text()
    python = sys.executable
    reads = [
        flag
        for prefix in dict.fromkeys((sys.prefix, sys.base_prefix))
        for flag in ("--read", prefix)
    ]
    command = [
        python, "-I", str(SANDBOX), *reads,
        "--execute", python, "--execute", os.path.realpath(python),
        "--write", str(Path.cwd()), "--cpu-seconds", str(seconds),
        "--", python, "-I", str(formula), *arguments,
    ]  # fmt: skip
    run = subprocess.run(command, capture_output=True, text=True)
    if run.returncode:
        sys.exit(f"sat-solve: the script failed:\n{run.stderr[-4000:]}")
    return run.stdout


def solve(text: str, seconds: float, extra: list[list[int]] = ()) -> tuple[str, list[int]]:
    """The answer of CaDiCaL on the toolchain of the working directory for
    `text` with the clauses `extra`, and a satisfying assignment."""
    if extra:
        header = re.search(r"^p cnf (\d+) (\d+)", text, re.M)
        text = (
            text[: header.start()]
            + f"p cnf {header[1]} {int(header[2]) + len(extra)}"
            + text[header.end():]
            + "".join(" ".join(map(str, clause)) + " 0\n" for clause in extra)
        )
    run = subprocess.run(
        [str(cadical()), "-q", "-t", str(max(1, round(seconds)))],
        input=text, capture_output=True, text=True,
    )  # fmt: skip
    if run.returncode == UNSATISFIABLE:
        return "UNSATISFIABLE", []
    if run.returncode != SATISFIABLE:
        return "UNKNOWN", []
    literals = [
        int(value)
        for line in run.stdout.splitlines()
        if line.startswith("v ")
        for value in line.split()[1:]
    ]
    return "SATISFIABLE", [literal for literal in literals if literal]


def cadical() -> Path:
    prefix = subprocess.run(
        [str(ELAN_HOME / "bin" / "lean"), "--print-prefix"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    return Path(prefix) / "bin" / "cadical"


def lrat(text: str, name: str, seconds: int) -> tuple[str, list[str]]:
    """For an unsatisfiable formula, NAME.lean with `lrat_proof NAME`: a
    theorem over one proposition per variable, in order, stating that some
    clause is false."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        sys.exit("sat-solve: --lrat needs a Lean identifier")
    clauses = "\n".join(
        line for line in text.splitlines() if line.strip() and not line.startswith("c")
    )
    if shutil.disk_usage(".").free < MIN_FREE_BYTES:
        sys.exit("sat-solve: less than 5 GB free; no certificate is written")
    formula, proof = Path(f"{name}.cnf"), Path(f"{name}.lrat")
    formula.write_text(clauses + "\n")
    start = time.time()
    solver = subprocess.Popen(
        [str(cadical()), "-q", "--lrat=1", "--no-binary", "-t", str(seconds),
         str(formula), str(proof)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    oversized = False
    while solver.poll() is None:
        time.sleep(0.5)
        if proof.exists() and proof.stat().st_size > MAX_CERTIFICATE_BYTES:
            solver.kill()
            solver.wait()
            oversized = True
    took = time.time() - start
    if oversized or solver.returncode != UNSATISFIABLE:
        formula.unlink(missing_ok=True)
        proof.unlink(missing_ok=True)
        if oversized:
            return (
                f"UNKNOWN ({took:.1f} s); the certificate exceeded "
                f"{MAX_CERTIFICATE_BYTES // 2**20} MB and was discarded: "
                "break symmetries or split the formula"
            ), ["UNKNOWN"]
        answer = "SATISFIABLE" if solver.returncode == SATISFIABLE else "UNKNOWN"
        return f"{answer} ({took:.1f} s); no certificate", [answer]
    certificate = " ".join(proof.read_text().split())
    Path(f"{name}.lean").write_text(
        f'lrat_proof {name}\n  "{" ".join(clauses.split())}"\n  "{certificate}"\n'
    )
    formula.unlink()
    proof.unlink()
    return (
        f"UNSATISFIABLE ({took:.1f} s); wrote {name}.lean "
        f"({len(certificate) // 1000} kB of certificate). It needs "
        "`import Mathlib.Tactic.Sat.FromLRAT`; its theorem binds one proposition "
        "per variable, variable 1 first, and states that some clause is false."
    ), ["UNSATISFIABLE"]


def names_of(text: str) -> dict[int, str]:
    return {
        int(number): name
        for number, name in re.findall(r"^c var (\d+) (\S+)", text, re.M)
    }


def assignment(names: dict[int, str], literals: list[int]) -> str:
    """The assignment by the names of `c var` lines, or as literals."""
    if not names:
        return "v " + " ".join(map(str, literals))
    return "\n".join(
        f"{names[abs(literal)]} = {str(literal > 0).lower()}"
        for literal in literals
        if abs(literal) in names
    )


def timed(text: str, seconds: float, extra: list[list[int]] = ()) -> tuple[str, list[int], float]:
    start = time.time()
    answer, literals = solve(text, seconds, extra)
    return answer, literals, time.time() - start


def single(text: str, seconds: int) -> tuple[str, list[str]]:
    answer, literals, took = timed(text, seconds)
    reply = f"{answer} ({took:.1f} s)"
    if literals:
        reply += "\n" + assignment(names_of(text), literals)
    return reply, [answer]


def sweep(options, seconds: int) -> tuple[str, list[str]]:
    """One line per size: the answer and its time."""
    low, high = (int(x) for x in options.sweep.split(".."))
    sizes = range(low, min(high, low + MAX_SWEEP - 1) + 1)
    deadline = time.time() + seconds
    lines, answers = [], []
    for size in sizes:
        left = deadline - time.time()
        if left < 1:
            lines.append(f"{size}: not decided (total time limit of {seconds} s)")
            break
        text = formula_text(options.formula, [*options.arguments, str(size)], seconds)
        answer, _, took = timed(text, left)
        answers.append(answer)
        lines.append(f"{size}: {answer} ({took:.1f} s)")
    return "\n".join(lines), answers


def enumerate_all(text: str, limit: int, seconds: int) -> tuple[str, list[str]]:
    """Distinct assignments of the named variables, by blocking clauses."""
    names = names_of(text)
    if not names:
        sys.exit("sat-solve: --all needs `c var` lines naming the variables to count")
    deadline, found, blocking = time.time() + seconds, [], []
    answer = "SATISFIABLE"
    while len(found) < limit:
        left = deadline - time.time()
        if left < 1:
            answer = "UNKNOWN"
            break
        answer, literals, _ = timed(text, left, blocking)
        if answer != "SATISFIABLE":
            break
        named = [literal for literal in literals if abs(literal) in names]
        found.append(named)
        blocking.append([-literal for literal in named])
    if answer == "UNSATISFIABLE":
        head = f"exactly {len(found)} assignment(s) of the named variables"
    elif answer == "UNKNOWN":
        head = f"at least {len(found)} assignment(s); the time limit ended the count"
    else:
        head = f"at least {len(found)} assignment(s); the limit of {limit} ended the count"
    shown = "\n".join(
        f"#{index + 1}: " + " ".join(
            f"{names[abs(literal)]}={'1' if literal > 0 else '0'}" for literal in named
        )
        for index, named in enumerate(found[:20])
    )
    return head + ("\n" + shown if shown else ""), [answer]


def split(text: str, variables: str, seconds: int) -> tuple[str, list[str]]:
    """The answer and time of each case of the named variables."""
    numbers = {name: number for number, name in names_of(text).items()}
    chosen = []
    for item in variables.split(","):
        item = item.strip()
        if item in numbers:
            chosen.append(numbers[item])
        elif item.lstrip("-").isdigit():
            chosen.append(abs(int(item)))
        else:
            sys.exit(f"sat-solve: unknown variable {item!r}")
    if len(chosen) > MAX_SPLIT:
        sys.exit(f"sat-solve: at most {MAX_SPLIT} variables to split on")
    label = {number: name for name, number in numbers.items()}
    deadline, lines, answers = time.time() + seconds, [], []
    for signs in itertools.product((True, False), repeat=len(chosen)):
        case = [v if s else -v for v, s in zip(chosen, signs)]
        left = deadline - time.time()
        description = ", ".join(
            f"{label.get(abs(l), abs(l))} = {str(l > 0).lower()}" for l in case
        )
        if left < 1:
            lines.append(f"{description}: not decided (total time limit)")
            answers.append("UNKNOWN")
            continue
        answer, _, took = timed(text, left, [[l] for l in case])
        answers.append(answer)
        lines.append(f"{description}: {answer} ({took:.1f} s)")
    return "\n".join(lines), answers


def main(arguments: list[str]) -> str:
    parser = argparse.ArgumentParser(prog="sat-solve")
    parser.add_argument("formula", type=Path)
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--seconds", type=int, default=60)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--sweep", help="A..B: each size, appended to the arguments")
    modes.add_argument("--all", type=int, help="enumerate up to K assignments")
    modes.add_argument("--split", help="decide each case of these variables")
    modes.add_argument("--lrat", help="write NAME.lean with a kernel-checked proof")
    options = parser.parse_args(arguments)
    seconds = max(1, min(options.seconds, MAX_SECONDS))
    start = time.time()
    if options.sweep:
        if options.formula.suffix != ".py" or not re.fullmatch(r"\d+\.\.\d+", options.sweep):
            sys.exit("sat-solve: --sweep A..B needs a script and two sizes")
        reply, answers = sweep(options, seconds)
        variables = clauses = None
    else:
        text = formula_text(options.formula, options.arguments, seconds)
        header = re.search(r"^p cnf (\d+) (\d+)", text, re.M)
        if not header:
            sys.exit("sat-solve: no `p cnf <variables> <clauses>` line")
        variables, clauses = int(header[1]), int(header[2])
        if options.all:
            reply, answers = enumerate_all(text, min(options.all, MAX_ALL), seconds)
        elif options.split:
            reply, answers = split(text, options.split, seconds)
        elif options.lrat:
            reply, answers = lrat(text, options.lrat, seconds)
        else:
            reply, answers = single(text, seconds)
    if log := os.environ.get("SAT_SEARCH_LOG"):
        mode = next((m for m in ("sweep", "all", "split", "lrat") if getattr(options, m)), "solve")
        with open(log, "a") as records:
            record = {
                "time": round(start),
                "formula": str(options.formula),
                "arguments": options.arguments,
                "mode": mode,
                "variables": variables,
                "clauses": clauses,
                "answer": answers[0] if len(answers) == 1 else answers,
                "seconds": round(time.time() - start, 1),
            }
            records.write(json.dumps(record) + "\n")
    return reply[:MAX_REPLY]


if __name__ == "__main__":
    print(main(sys.argv[1:]))
