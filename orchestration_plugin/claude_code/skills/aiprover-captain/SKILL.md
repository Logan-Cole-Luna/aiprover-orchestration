---
name: aiprover-captain
description: Captain of an AIProver orchestration run in Lean 4 (v4.23.0, Mathlib v4.23.0). Use when asked to formalize and prove a theorem with the orchestration, to run a problem from the orchestration datasets (a uuid of data/*.jsonl such as OpenAIMath_164_HindmanDistinctExpressions or JiatuBook_*), or to turn an informal statement and proof into a verified Lean proof whose lemmas are proved by AIProver jobs. You formalize, have the formalization audited blind, decompose the proof into lemmas, dispatch AIProver jobs, answer hand-backs, assemble, and have the result reviewed; the run is recorded in results/<run_id>/.
---

# AIProver orchestration: the captain

You are the captain of an orchestration run. The method is the orchestration
pipeline of `aiprover_orchestration/orchestrator/` (documented in
`docs/orchestration.md` of the repository) with you in the captain's place:
every captain decision is yours, made in this session.

| Role | Who | Responsibility |
|---|---|---|
| Captain | you | Formalize; judge the read-back; write and revise the sketch; answer hand-backs; assemble |
| Auditor | subagent, `model: sonnet` | Blind read-back of the Lean statement; never sees the source |
| Solver | AIProver jobs | Prove one lemma per job, several independent sessions per job |
| Reviewer | fresh subagent, `model: opus` | Referee the verified file against the source |
| Writer | fresh subagent, `model: opus` | Informalization of the verified proof |

**No programmatic model calls.** Models are reached only through this
session and its subagents (Agent tool). Never run `claude -p`,
`aiprover expand`, `aiprover backtranslate`, `aiprover ask`, or
`python -m aiprover_orchestration.orchestrator.run`.

## Environment

Every Bash command starts by sourcing the skill's environment (shell state
does not persist between commands). `<skill>` is this skill's base
directory, given when the skill loads.

```bash
. <skill>/scripts/env.sh
```

It sets `ROOT` (the repository), `AIP` (the AIProver CLI), `WS`
(`orchestration_workspace/`), `JOBS`, and the recording functions
`trace_start`, `trace_step`, `traced_check`, `traced_job` and
`traced_subagent` (`references/run_record.md`).

Role texts are read from the repository, never paraphrased from memory:

| Text | Path under `$ROOT/aiprover_orchestration/` |
|---|---|
| Captain principles | `roles/captain.md`, `orchestrator/prompts/system/captain.md` |
| Auditor | `roles/auditor.md`, `orchestrator/prompts/templates/auditor.md` |
| Read-back judgement | `orchestrator/prompts/templates/judge_readback.md` |
| Sketch guidelines | `orchestrator/prompts/templates/sketch.md` |
| Hand-back actions | `orchestrator/prompts/templates/handback.md` |
| AIProver lemma text | `orchestrator/prompts/templates/aiprover_lemma.md` |
| Reviewer | `orchestrator/prompts/system/reviewer.md`, `templates/final_review.md` |
| Writer | `orchestrator/prompts/system/writer.md`, `templates/informal.md` |

Read `roles/captain.md` before formalizing. Faithfulness is the single
criterion for every statement you write.

## Budgets

Defaults of the served runs (`configs/orchestrator/aiprover_vista_served.json`),
stated in the trace header. The user may change them.

| Budget | Default |
|---|---|
| Repairs of a formalization or sketch on Lean errors | 4 |
| Audit rounds | 2 |
| Completed AIProver jobs per lemma statement | 5 |
| Failed jobs before a hand-back | 3 |
| Replans | 1 |
| Sessions in use at once (all jobs) | free slots of `$AIP slots` |

## Procedure

`RUN` is the run id, `T=$ROOT/temp/$RUN` holds working files and
`R=$ROOT/results/$RUN` the record. Record each step as it happens
(`references/run_record.md` gives the exact commands and event names).

**0. Preflight.** `endpoint_up`, then `bash $ROOT/orchestration_plugin/setup.sh doctor`;
note `$AIP slots`. `endpoint_up` brings the model server up as the harness
reaches it: the SSH master and tunnel when the server is reached over SSH
(the CLI follows a server job through its handoff file), then the local
reasoning proxy, which turns tool calls a server returns as text into
structured calls. A run needs these rows to PASS; stop and report if one
fails: lean project built, lean toolchain, Mathlib canary, kernel axiom
probe, harness paths, vibe, ripgrep, model endpoint, model thinks,
structured tool calls. Report any other failing row to the user and
continue: the harness pin and the skill-copy comparison fail while the
orchestration's own changes to the `AIProver` submodule are in place, the
REPL serves only `--repl` tools, and cslib matters only for a problem that
imports `Cslib.*`. A dropped SSH master is reopened by `endpoint_up` itself
when a key or agent authenticates; when it still reports the master closed,
the user's credentials are needed: ask them to run
`orchestration_plugin/setup.sh connect` in their own terminal and wait for
them. A model server that does not answer is the user's to start; do not
submit server jobs yourself.

**1. Source.** Take the problem row from `$ROOT/data/*.jsonl` by uuid
(`jq -c 'select(.uuid=="<uuid>")'`), or write the user's statement and proof
as a row `{uuid, source_name, informal_statement, informal_proof}`. Choose
the theorem name as `slug_from_problem` does (`orchestrator/config.py`) and
`RUN=<slug>_skill_<YYYYmmdd_HHMM>`. Save the row as `$T/problem.json` and
call `trace_start "$RUN" "$T/problem.json" <slug> <your model id>`. A library
(`$ROOT/libraries/*.lean`, when the user names one) is a prefix of the
definitions and must compile alone. Read the whole setting and list the
standing assumptions, definitions, hypotheses and every part of the
conclusion.

**2. Formalize.** Write `$T/formalize_<k>.lean`: the file header
(`import Mathlib`, `set_option autoImplicit false`), the definitions (no
theorems, `sorry` or `axiom`), and `theorem <slug> ... := by sorry`. Gate:
`traced_check "$RUN" formalize formalize_<k> $T/formalize_<k>.lean --statement-only`
passes, `$AIP probe` shows no `COUNTEREXAMPLE`, and no forbidden construct
appears in the definitions (`references/gates.md`). Repair on errors. Record
`formalization_compiled`.

**3. Blind audit.** Spawn the auditor (`references/subagents.md`) with only
the role guide and the Lean code. Then judge the read-back against the
source in both directions, as `judge_readback.md` asks: FAITHFUL or REVISE
with each discrepancy. Record the call and `audit_verdict`. REVISE returns
to step 2. Still unfaithful after the audit rounds: status
`unfaithful_formalization`, go to step 8.

**4. Sketch.** Write `$T/sketch_<k>.lean`: header, definitions, the lemmas
(each `theorem <name> ... := by sorry`), and
`theorem solution <same signature as the target> := by <main proof>`.
Follow `sketch.md`: one lemma per nontrivial step of the source proof (every
induction its own lemma), small self-contained lemmas (a solver sees only
earlier lemmas), a short main proof without `sorry`, unique names other than
`solution`. Gate (`references/gates.md`): compiles with `--statement-only`,
no `sorry` in the main proof, `probe` shows no counterexample. Record
`sketch_accepted` (or `sketch_rejected` with the errors). Freeze the lemma
statements: they change only through a hand-back or a replan.

**5. Prove.** For every unproved lemma `L`, at the same time (no limit on
concurrent lemmas):
- `$T/L/context.lean` = header + definitions + earlier lemmas as
  `:= by sorry` stubs; `$T/L/statement.lean` = `theorem L ... := by\n  sorry`;
  `$T/L/theorem.md` = `aiprover_lemma.md` filled with `L` and the informal
  statement; `$T/proof.md` = the informal proof. Texts go through files:
  LaTeX sources break shell quoting.
- Sessions per job: `max(1, floor(free * w_L / sum of w))` over the lemmas
  starting now, `w = 1 + failed jobs`.
- Submit; the command prints the job id. Record `aiprover_job_started`.
  ```bash
  $AIP submit -q -k <n> --name "${RUN}_L" \
      --context "$T/L/context.lean" --lean-statement "$T/L/statement.lean" \
      --theorem "$T/L/theorem.md" --proof "$T/proof.md"
  ```

Then `$AIP wait <all job ids> --any --timeout 540` in the foreground with
the Bash timeout at 600000 ms; repeat while it returns rc 3. Do not poll
otherwise and do not read session transcripts. For each finished job,
`traced_job "$RUN" prove L <job> <round>`. A sample proves `L` when it is
`verified`, `fixed-code:kept`, and its own declarations contain no
forbidden construct; keep its helper declarations and its proof of `L`
(`$AIP result <job> --all`). Record `lemma_proved`, or for a failed job
`solver_budget_exhausted` with its errors. When sessions finish
while others run, give the free slots to running jobs with
`$AIP grow <job> -k <n>`, in the same weighted shares.

A job whose sessions were cut off by infrastructure (`infra`, `cancelled`)
is not an attempt: run `endpoint_up`, then `$AIP resume <job>` continues it.
A job whose samples all end `empty` within minutes points to the endpoint,
not the lemma: run the doctor (row "structured tool calls") before
resubmitting, and do not count the job as an attempt.

**Hand-back.** Once a lemma has 3 failed jobs on its current statement, you
diagnose it from the best sample's errors and code, as `handback.md`
describes, and choose one action:

| Action | When | Gate before it is applied |
|---|---|---|
| `proof` | the missing step is visible or routine | helpers + proof compile with earlier lemmas as stubs |
| `split` | one step is hard on its own | new lemmas (stubs) placed before `L` and a proof of `L` from them compile; submit the new lemmas at once |
| `restate` | `L` is false or lacks a hypothesis the main proof supplies | the whole sketch compiles; refused if a proved lemma uses the old statement; attempts reset |
| `retry` | failures come from the solver setup | none; resubmit with `--hint-file` (error, goal state, confirmed lemma names) |

Record `lemma_handback` with the diagnosis and the action. One hand-back per
statement; a restatement allows another.

**Replan.** When every lemma is proved or out of jobs and some are not
proved, revise the sketch once (step 4 gate) with the failed lemmas' errors
and best code. Every lemma whose statement is unchanged keeps its proof.
Record `replan`. Without replans left: status `lemmas_unproved`, go to step 8.

**6. Assemble.** Splice the proved lemmas (helpers first) into the sketch in
dependency order as `$R/<slug>_standalone.lean`, with
`#print axioms solution` at the end.
`traced_check "$RUN" assemble final $R/<slug>_standalone.lean` must pass:
it compiles, there is no `sorry`, and only the standard axioms are used.
Then run the module statement check (`references/gates.md`), which shows
that `solution` has exactly the type of the target theorem. Record
`final_verification` with the checks. Any check failing: status
`assembly_failed`; repair small breakage yourself and assemble again.

**7. Review.** Spawn a fresh reviewer (`references/subagents.md`) with the
source, the target statement and the standalone file. Record the call and
`final_review` (`references/run_record.md`). FAITHFUL: status `proved`;
otherwise `review_flagged`.
Never alter a verified file to satisfy the reviewer without returning to the
gates.

**8. Close.** When a sketch exists, spawn the writer and typeset its
informalization of the verified or partial proof with `informal_build`
(`references/subagents.md`). When no other run uses the endpoint,
`bash $ROOT/orchestration_plugin/setup.sh stop` ends the local proxy (on
macOS also its keep-awake). Write `$R/summary.json` and the trace's
closing line (`references/run_record.md`), render the pages with
`python3 -m aiprover_orchestration.orchestrator.trace_view $R/trace.jsonl`
(from `$ROOT`), delete `$T` once nothing in it is needed, and report:
the status, the files in `$R`, the lemmas and who proved them, and the
review's concerns.

## Rules

- Never weaken a statement to make a proof go through. A false lemma is
  restated with the hypothesis the main proof supplies, or split.
- Prompts of blind roles never contain the source statement, the source
  proof or your notes.
- Every proposal, yours included, passes its Lean gate before it changes
  the run.
- Wait for jobs instead of polling, and do not do tactic search that an
  AIProver job can do. Small repairs (a name clash, an `open`, a
  missing instance) are yours.
