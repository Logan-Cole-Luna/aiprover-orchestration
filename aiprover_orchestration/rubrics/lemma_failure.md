# Failed lemma: cause

Adapted from the proof-integrity rubric of autoform-bot (`skills/agent-review/references/proof-integrity.md`, MIT license, https://github.com/facebookresearch/autoform-bot).

The unit is one prover job (or prover phase) that ended without a proof of a lemma. The grade is a cause category with a reason of at most three sentences naming the step or fact that blocked the proof. The categories fall into two levels. A failure of strategy calls for a new plan (a replan, another approach, a restatement); a failure of execution calls for more or better proving of the same plan.

## Categories

Strategy, the plan is at fault:
- `false_or_misstated`: the statement is false, or lacks a hypothesis or has a wrong quantifier, so that no proof exists as stated.
- `open_step`: the statement is plausible, yet no argument for it is known at this granularity: a gap of the drafted proof, an open problem, or a step the plan asserts without proof.
- `too_coarse`: true, with a known argument within Mathlib's reach, yet bundling several nontrivial results, so that one job cannot reach it; it needs a split.

Execution, the plan is sound:
- `missing_support_or_encoding`: true, but a needed body of theory or a needed fact is absent from Mathlib, or the encoding has little Mathlib support. A split does not cure this category, which distinguishes it from `too_coarse`.
- `prover_capability`: true, of reasonable size, with the needed support available; the sessions had enough turns, yet chose a wrong route or failed on Lean mechanics.
- `budget_or_infrastructure`: the sessions were starved or cut before the question was settled: few model turns within the time limit (decided mechanically, item 2), failed model calls, a server loss, or a harness rejection unrelated to the mathematics.

`other`: a cause that fits none of these (for example a sketch that orders a needed lemma after the failing one), named in the reason.

## Evidence, checked first

1. The hindsight label, computed from later trace events, is mechanical: refuted later means false; restated and then proved means misstated; split and all parts proved means too coarse; proved later by the orchestrator means prover capability; proved later by another prover job means budget; a lemma the plan named as a gap (`gap_`) that stayed unproved means open step. A label is overridden only when the session evidence contradicts it, and the reason then says why.
2. Session activity is mechanical: the minutes, model turns and failed calls of each session. A job is starved when each of its sessions either ran fewer than 38 model turns, the number within which three quarters of verified proofs are found, or ended on an infrastructure error; the grading script assigns it `budget_or_infrastructure` with the flag `uncertain` without consulting the grader, unless the hindsight label is a strategy category, since more turns might have succeeded but the evidence does not show it. Turn rate is not used, because verified and failed sessions run at overlapping rates. A job that reaches the grader was given at least that many turns in some session, and `budget_or_infrastructure` then requires other evidence: failed model calls such as read timeouts, a server loss, or a harness rejection unrelated to the mathematics. A session that ran 76 model turns or more (the 90th percentile of turns to a proof) and failed is a failure of prover capability, or of granularity when the statement bundles several results.
3. The samples (status, timeouts, problems) and the final Lean of each session are read next: a `sorry` left at one step locates the difficulty; errors on API names after many turns point to prover capability.
4. Only then does the grader judge the truth and difficulty of the statement from the statement and the followed proof, and whether the blocking fact is in Mathlib.
5. The choice between `missing_support_or_encoding` and `too_coarse` requires naming the absent Mathlib theory or fact (for example a named theorem, construction or body of theory that Mathlib does not contain) or the encoding with little support. When no such name can be given, the grader does not guess between the two: it gives the category the evidence favours and answers `<uncertain>yes</uncertain>`, and the level is recorded as uncertain. In every other case it answers `<uncertain>no</uncertain>`.

Precedence when several apply: false, open step, missing support, too coarse, prover capability, budget.
