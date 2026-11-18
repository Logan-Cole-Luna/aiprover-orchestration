# Grading runs

A recorded run can be graded stage by stage with the rubrics in
`aiprover_orchestration/rubrics/`, which adapt the agent-review rubrics of
[autoform-bot](https://github.com/facebookresearch/autoform-bot) (MIT
license). The grades locate where a run went wrong and why, and they measure
the quality of what a successful run produced. Grading is offline: it reads
`results/<run>/` and changes no run.

## Rubrics

| Rubric | Unit | Grade |
| --- | --- | --- |
| `faithfulness` | Formal statement against the source | 0 to 5 |
| `draft` | Proof drafted in prove mode | Correctness, completeness, approach, 0 to 5 each |
| `sketch` | Lemmas and main proof against the proof they follow | Fidelity, decomposition, encoding |
| `lemma_failure` | Prover job that ended without a proof | Cause category, as strategy or execution |
| `lemma_proof` | Accepted lemma proof | Quality, 0 to 5 |
| `escalation` | Orchestrator decision on a stuck lemma | Whether the action was right, with hindsight |
| `final` | Verified result | Faithfulness and proof, 0 to 5 each |

Each rubric gives anchors for every score, the evidence to establish first,
hard ceilings, and pass and reject thresholds; axes are scored separately and
never averaged. The causes of a failed lemma fall into two levels: a failure
of strategy (a false or misstated lemma, an open step, a lemma too coarse for
one job) calls for a new plan, whereas a failure of execution (missing Mathlib
support, prover capability, budget or infrastructure) calls for better
proving of the same plan.

## Evidence and judgment

What the trace and Lean settle is computed without a model: compilation,
the fate of every lemma later in the run (proved, split, restated, refuted),
the turns and failed calls of each prover session, declared gaps, and
statistics of accepted proofs. A lemma later refuted was false; one split
into parts that were all proved was too coarse; a job whose sessions each ran
fewer than 38 model turns, the number within which three quarters of verified
proofs are found, is labeled as budget without a model call. Only the
remaining judgment goes to the grader, a configurable agent that is not the
run's own orchestrator model by default. The evidence of each unit has a
fixed size, so a run with long proofs or many lemmas is graded at the same
cost per unit.

## Usage

```bash
PY=aiprover/nix/tools/bin/python3
$PY -m aiprover_orchestration.orchestrator.grade results/<run> --dry-run          # units and evidence, no calls
$PY -m aiprover_orchestration.orchestrator.grade results/<run> --kinds draft sketch \
    --agent '{"backend": "claude", "model": "<model>"}'                          # grade, at most one call per unit
$PY -m aiprover_orchestration.orchestrator.grade results/<run> --compare REFERENCE.jsonl
```

Grades are appended to `results/<run>/grades.jsonl` and the grader's calls to
`grade_calls.jsonl`. `--units` selects units by the identifiers that
`--dry-run` lists, `--limit` bounds the number of calls, and a call refused
on a usage limit stops the grading at once. `--compare` reports exact and
within-one agreement of scores and agreement of failure categories with a
file of reference grades.

## Calibration

On 57 reference units from ten runs, in both settings and including two
proved and four failed prove-mode runs, the grader agreed with the reference
grades on 79 percent of scores exactly and on 97 percent within one point,
and on the level of 16 of 17 failures. Agreement was lowest for sketches (67
percent exact, 92 percent within one), all on the reduction sketches of an
open problem; grades of such sketches are read within one point.
