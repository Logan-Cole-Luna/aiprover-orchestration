# Faithfulness of the formal statement

Adapted from the faithfulness rubric of autoform-bot (`skills/agent-review/references/faithfulness.md`, MIT license, https://github.com/facebookresearch/autoform-bot).

The unit is the formalization of one run: the Lean definitions and the theorem statement, compared with the source statement. Proof quality is not graded here. In prove mode a statement given by the problem is kept verbatim and is not a unit; a statement the run formalized is graded as in autoformalization. Faithfulness is a supporting check of proof synthesis: an unfaithful statement makes every later proof worthless, whatever its quality.

## Evidence, checked first

1. The trace settles that the file compiles and which round of the audit produced it; these facts are not re-examined.
2. The grader reads the source statement itself and the complete Lean text (definitions, statement, notes), not a summary or a read-back.
3. Quantifiers, hypotheses, domains, conclusions and edge cases (division by zero, truncated subtraction, empty types, degenerate parameters) are compared clause by clause in both directions.
4. Each definition specific to the source must have a substantive body; a standard Mathlib concept is correct when it matches the source concept.

When the source statement is absent the grade is insufficient evidence, not a score.

## Common failures

A dropped conclusion; a restricted or reordered quantifier; an added hypothesis that is not redundant; a different type, topology, measure or finiteness condition; a proxy quantity never connected to the source quantity; content placed in a hypothesis or class field; a hollow or vacuous definition; a statement of an intermediate goal of the source proof (what the proof reduces the claim to) in place of the claim itself.

## Scores

| Score | Standard |
|---:|---|
| 5 | Same objects, quantifiers, hypotheses and conclusions; every part is represented and supporting definitions are genuine. |
| 4 | Very close; only redundant or implementation-level assumptions such as decidability differ. |
| 3 | Equivalent with superficial differences (naming, coercions, a harmless stronger typeclass), or a faithful encoding of a deliberately narrowed source claim that the notes state. |
| 2 | Some structure matches, but a domain, hypothesis, conclusion or modeling choice materially changes the theorem. |
| 1 | Major strengthening, weakening or quantifier mismatch. |
| 0 | Unrelated, vacuous or hollow statement. |

Hard ceilings: a wrong domain or type, a missing conclusion, a nonredundant hypothesis change, or content hidden in a class or hypothesis scores at most 2. A statement that replaces the source claim by an intermediate goal of its proof is a modeling change that materially changes the theorem and scores at most 2, since the step from the claim to that goal is neither stated nor checked; the ceiling does not apply when the formalization defines the source's objects and the goal follows from the claim by definitional unfolding alone. Pass at 4; reject at 2 or below.
