# Isolated benchmark

Controlled, measured runs of one model session per problem, separate from the
orchestration. Each session starts from the same prompt and from nothing else.

## Files

| File | Purpose |
|---|---|
| `problems.json` | Benchmark problems and their dataset rows |
| `conditions.json` | Model, effort, tools, spend cap, and time cap of each condition |
| `prompts/` | Prompt template of each condition |
| `confine.py` | Landlock confinement of the session |
| `run.py` | One session, with its record |
| `grade.py` | Lean check and review of a finished session |
| `report.py` | Table of all runs |

## Isolation

The session runs under Landlock, on `aiprover_orchestration/lean/sandbox.py`.
It holds an empty home, a replaced environment, and no project or user
settings. Its only link to the caller's home is the credentials file.

| Resource | Access |
|---|---|
| Repository, results, libraries, caller's home | None |
| Lean toolchain, shell, other executables | None; only the CLI itself executes |
| Files | Read and write in its run directory only |
| Network | TCP to port 443 only, for the model API |

The condition `no_lean` also offers only file tools, each scoped to the working
directory, so the session has no command or web tool. Landlock does not cover
UDP, which name resolution uses, and port 443 is not limited to one host.

The condition `lean` adds a Lake project on the workspace's packages, the
shell command `lake env lean` with the filters `head`, `tail`, and `grep`, and
read access to the Mathlib sources. Only the toolchain, Git, the shell, and the
filters may execute, so no network client is reachable.

## Procedure

    python3 benchmark/run.py 016 --repeat 0
    python3 benchmark/grade.py benchmark/runs/016/no_lean/r0
    python3 benchmark/report.py

Records lie in `benchmark/runs/<problem>/<condition>/r<repeat>/` and are never
overwritten. A record holds the prompt, a manifest with the prompt's hash and
the CLI version, the transcript, the measured cost, tokens, turns, and tool
calls, and the session's files.

## Grading

The grader elaborates `Solution.lean` once, under the Lean sandbox. A proof
counts only if it compiles without `sorry`, on the standard axioms, and is
accepted by review. The review applies the final review of the orchestration
and the FL-FL and NL-NL judge prompts under Claude. Judge2 of the dual-judge
evaluation requires Bedrock and is not run here.
