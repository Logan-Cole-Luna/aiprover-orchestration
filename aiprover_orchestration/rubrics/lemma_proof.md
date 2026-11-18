# Proved lemma: proof quality

Adapted from the code-quality and Mathlib-style rubrics of autoform-bot (`skills/agent-review/references/code-quality.md`, `mathlib-style.md`, MIT license, https://github.com/facebookresearch/autoform-bot).

The unit is one accepted proof of a sketch lemma, by a prover or by the orchestrator. Lean has accepted it with standard axioms only; soundness is settled and is not graded here. The grade concerns maintainability and economy, which predict whether proofs of harder lemmas will reuse it and whether a library built from it remains usable.

## Evidence, checked first

The mechanical statistics are given: lines of statement and proof, the number of automation calls (`simp`, `omega`, `aesop`, `decide` and similar), and helpers declared but not referenced. The grader then reads the statement, the helpers and the proof.

## Review points

- Length relative to the statement: a proof far longer than the mathematics requires (repeated case work, re-derived library facts) is penalized.
- Reuse of Mathlib and of earlier lemmas: a short proof by an exact library result is preferred to a reconstruction.
- Brittle automation: nonterminal `simp` without `only`, `decide` or `native_decide` on large instances, long `aesop` or `omega` chains whose success depends on the local context.
- Unused helpers, dead `have` steps, and leftovers of failed attempts.
- Readability: named intermediate steps where the argument is nontrivial.

## Scores

| Score | Standard |
|---:|---|
| 5 | Short, idiomatic, uses the right library results; nothing to remove. |
| 4 | Clean with minor issues: a nonterminal `simp`, a step that a library lemma would shorten. |
| 3 | Correct but noticeably long or brittle; worthwhile cleanup. |
| 2 | Several of: far too long, re-derives library facts, fragile automation, unused helpers. |
| 1 | Opaque; a maintainer would rewrite it. |
| 0 | Dominated by anti-patterns (dead code, giant case splits by automation). |

Pass at 3. Whether the lemma itself was a good lemma belongs to the sketch rubric and does not enter this score. Alternatives left in `first | ... | ...` combinators count as brittle automation.
