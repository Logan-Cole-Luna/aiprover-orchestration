# Escalation decision

The unit is one decision of the orchestrator on a lemma the provers could not prove: prove it directly, split it, restate it, retry it, or refute it. The decision is judged with hindsight: the trace shows what followed. The rubric follows the evidence and scoring conventions of autoform-bot's agent-review rubrics (MIT license, https://github.com/facebookresearch/autoform-bot).

## Evidence, checked first

1. Mechanical outcome: a direct proof or a refutation was accepted by Lean; for a split, how many new lemmas were proved later; for a retry or a restatement, whether the lemma was proved later. The hindsight label (`effective` or `ineffective`) summarizes it.
2. The grader reads the prover failures that led to the escalation, the orchestrator's diagnosis, the action's content (new lemmas, restated statement) and the followed proof.

## Judgment

The question is whether the action matched the true cause of the failure, as far as the evidence reveals it. A split is right when the lemma bundled separable results and each part is easier; it is wrong when one part keeps the whole difficulty, when the parts need theory absent from Mathlib, or when the lemma was false. A retry is right only for a failure of budget or infrastructure. A restatement is right only when the statement was false or misstated. A direct proof is right for a routine lemma. A diagnosis that names a cause the evidence contradicts lowers the score even when the action happens to succeed. An ineffective outcome does not by itself make the decision wrong: a split into true, well-sized parts that the provers then failed is a reasonable decision; the reason says so.

## Scores

| Score | Standard |
|---:|---|
| 5 | Correct diagnosis and the best available action; the outcome confirms it. |
| 4 | Correct diagnosis; the action works but a simpler one was available. |
| 3 | Plausible diagnosis and action, outcome inconclusive, or effective but by a long route. |
| 2 | Diagnosis or action misses the main cause, so that the difficulty is moved without being removed. |
| 1 | Action contrary to the evidence (a retry of a false lemma, a split of an open problem into open parts). |
| 0 | No usable action. |

Hard ceiling: a split whose parts include one at least as hard as the original lemma scores at most 2. When the evidence shows that no action could succeed (the lemma needs theory absent from Mathlib), the decision is graded on its diagnosis: a correct diagnosis followed by a split scores 2, and the reason records that the pipeline offered no better action.
