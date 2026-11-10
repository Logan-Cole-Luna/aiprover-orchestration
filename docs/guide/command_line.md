# Command line

Each submission of the web page is a process of
`aiprover_orchestration.orchestrator.run`, so the command line runs the same
pipeline and writes the same records to `results/<run_id>/`. A run needs a
problem, from a dataset or from files, and a config that names the models.

```bash
PY=aiprover/nix/tools/bin/python3
RUN="$PY -m aiprover_orchestration.orchestrator.run --config configs/orchestrator/standard.json"

# A problem from a dataset: a JSONL file of rows with `uuid`,
# `informal_statement`, and `informal_proof`.
$RUN --dataset data/openai_math.jsonl --problem-uuid OpenAIMath_164_HindmanDistinctExpressions

# A problem given as files: formalize the given proof.
$RUN --statement statement.tex --proof proof.tex --run-id my_result

# Prove a theorem without a given proof, on a library of earlier results,
# and add the proved result to that library.
$RUN --statement statement.tex --mode prove --library libraries/mine.lean --extend-library true

# Continue a run that stopped (cancelled, interrupted, or out of budget)
# from its trace, which records its problem and config.
$PY -m aiprover_orchestration.orchestrator.run --run-id my_result --resume
```

## Run options

| Flag | Effect |
| --- | --- |
| `--config FILE` | Models of each role and default settings: `standard.json` (the page's defaults, AIProver on the `./setup` server) or `claude.json` (hosted models only) |
| `--dataset FILE`, `--problem-uuid ID` | A problem from a dataset |
| `--statement FILE`, `--proof FILE` | A problem as files: an informal statement, and its proof if any |
| `--formal-statement FILE` | A Lean statement to prove as given (with `--mode prove`) |
| `--run-id ID` | Name of the run and of its folder in `results/` |
| `--resume` | Continue the run `--run-id` from its trace |
| `--fork-from RUN`, `--fork-stage STAGE` | Start a new run from another run's steps before a stage (default `prove`) |
| `--max-restarts N` | Automatic resumptions after infrastructure failures (default 2) |

## Settings

Every field of `orchestrator/config.py` is a flag of the same name, and every
setting of the page's "Orchestrator settings" is one of them; `--help` lists
all. The most used:

| Flag | Effect |
| --- | --- |
| `--mode autoformalize\|prove` | Formalize the given proof (default), or withhold it and prove the theorem |
| `--disproof true` | In prove mode, prove the negation if the theorem is judged false |
| `--library FILE`, `--extend-library true` | Build on a library of verified results, and add a proved, reviewed result to it |
| `--sketch-approval true` | Stop after the proof plan for review; on resume, `--sketch-approval false` approves (`--sketch-guidance TEXT` adds comments), and `--sketch-feedback TEXT --plan-revision N` asks for a revision |
| `--write-reports true` | Write the report, informal proof, and blueprint (also on resume) |
| `--paper-search true` | The orchestrator may search arXiv and read papers |
| `--workers N` | Prover chains per lemma |
| `--aiprover-attempts-per-lemma N` | Failed AIProver jobs before a lemma statement is given up |
| `--aiprover-escalation-after N`, `--max-escalations N` | Failed jobs before a lemma is escalated to the orchestrator, and escalations per run |
| `--aiprover-job-sessions N` | Parallel AIProver sessions per job |
| `--aiprover-guidance orchestrator\|sketch\|""` | What each job receives besides the source proof: the orchestrator's proof of the lemma, the sketch's comments, or nothing |
| `--aiprover-offline true` | Keep AIProver sessions off the internet |
| `--lemma-counterexamples true`, `--vacuity-check true`, `--sketch-judge true` | Test lemmas for counterexamples, check hypotheses for vacuity, and judge each sketch |
| `--max-hosted-calls N`, `--max-hosted-usd X` | Hosted-model budget of the run (0: unlimited) |

`AIPROVER_LEAN_RELEASE=<release>` runs on another Lean release, and
`AIPROVER_LIBRARY_PACKAGE=<folder>` makes a package library the workspace.

## Projects

A project is a file `data/projects/<name>.json` (format in
`orchestrator/project.py`) with a library in `libraries/`.

```bash
P="$PY -m aiprover_orchestration.orchestrator.project"
$P run <name> --config configs/orchestrator/standard.json    # prove its results in order
$P task <name> research "Which lemmas does the main theorem need?"
$P task <name> check "<claim>"                               # referee one claim
$P task <name> paper                                          # write the paper
$P review <name> 0 confirmed "checked by hand"               # decide on claim 0
```

## Other tools

```bash
$PY -m aiprover_orchestration.orchestrator.trace_view results/<run_id>/trace.json   # trace pages
$PY -m aiprover_orchestration.library verify NAME FILE [--disprove]                   # theorem library
$PY -m unittest discover -s tests -t .                                                # tests
```
