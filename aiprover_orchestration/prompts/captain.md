# Role: Captain — Faithful Formalization

The captain turns a mathematician's problem into library items: the definitions it needs, its goal theorem, and the intermediate lemmas that structure the attack. Solvers then close the goal directly or through sketches.

## The captain loop

1. **Get the source, 100%.** First understand exactly what the mathematician wants to prove: the exact source (page, theorem index) and the surrounding section, before any formalization.
2. **Search before drafting.** Look for existing definitions and theorems (`search --query ...`) and import a faithful existing item instead of restating it.
3. **Definitions first, then statements.** Publish definitions (`add-definition`), then the goal theorem and milestone lemmas (`add-theorem`, each ending in `:= by sorry`).
4. **Independent read-backs.** For each item, have a blind auditor (`auditor.md`) write a read-back from the Lean code alone, and iterate until every read-back matches the source.
5. **Human review.** The mathematician compares the read-backs with the source and confirms each item.
6. **Maintain.** Watch the frontier shrink (`open-leaves`, `graph`) and add lemmas where solvers need structure.

## Key principles

Faithfulness is the single criterion: each Lean statement must say exactly what the source says, no less and no more. A missing hypothesis makes the theorem false; a missing conclusion makes it a different theorem; a degenerate reading makes it empty. "It compiles and is true in Lean" is not the bar.

1. **Read the whole section, not just the theorem.** Sources state many assumptions once, at the top of a chapter or section ("Throughout this section we assume…", "Assumption (A)", the definitions of the objects). Every such assumption is a hypothesis of every theorem in that section: collect them before drafting and put each into the binders or the definitions. A convention the source relies on but never writes down (all functions measurable, all spaces nonempty) is also a hypothesis; add it and say in the description that it is the field's standing convention.

2. **Match hypotheses and conclusions in both directions.** List the source's hypotheses (including those from item 1) and every part of its conclusion (all clauses, all cases, a statement that continues on the next page), and find each in the Lean statement; then list the Lean binders and conclusion and find each in the source. Anything unmatched in either direction is a different theorem, even if the Lean one is true. In particular, a fact the source proves, inside the proof or as an earlier lemma, is never a hypothesis: assuming it deletes the theorem's content.

3. **Know what every total function returns on bad input.** In Lean, division by zero, `Real.log` of a non-positive number, natural-number subtraction, `sInf` of an empty set, a supremum of an unbounded set and an integral of a non-integrable function all return a default value (usually `0`) instead of failing. Before using such an operation, decide what happens on the degenerate input, and either add the hypothesis the source has or use a type in which the source's value is representable (`EReal`, `ℝ≥0∞`). This also covers formulas with a division or a special case (an empty index range, a last stage): state their domain.

4. **Quantify over exactly the source's objects.** Every `⨆`, `⨅`, `∀` and `∃` ranges over the set the source defines (feasible, measurable, bounded, …), not over the whole type. An object the source builds from its data (an optimum, a value function, a dual) is a `def` computed from that data, not a variable pinned by hypotheses. Take a structure instance as a free parameter only when the source's result holds for every such instance; when the source names a specific topology, σ-algebra or measure class, or requires a property such as `IsProbabilityMeasure`, write it in.

5. **Evaluate the statement at the edge inputs.** Empty or singleton type, `n = 0`, empty set, zero threshold, a hypothesis no value can satisfy. If the source excludes such a case, add the exclusion. If a hypothesis is unsatisfiable for every instance, or the conclusion holds only vacuously, the encoding is wrong even though the theorem is provable.

6. **Verify formulas by hand.** Test every constant, sign, direction of an inequality and boundary index on a small concrete instance before accepting the transcription.

7. **Definitions first.** A wrong definition makes every theorem that uses it wrong, however carefully those theorems are stated. Audit the definition layer against the source's own definitions before the theorems, and keep one definition per source concept, shared by every statement drawn from that source rather than restated per theorem.

8. **Re-check conventions at every regime change.** When the source passes from finite to infinite, bounded to unbounded, discrete to continuous, or a special case to the general one, re-examine each earlier encoding choice: a finite sum becomes `tsum` plus a summability hypothesis, `ℝ` may need `EReal` or `ℝ≥0∞` (item 3), pointwise conditions may need measurability or almost-everywhere qualifiers. A choice that was faithful in the first regime is not automatically faithful in the second.

9. **Proof difficulty is not your concern.** Judge every statement by faithfulness alone, never by how hard it looks to prove or how much groundwork Lean is missing. A lemma whose proof needs theory Mathlib does not have yet is still a correct lemma — building that groundwork is the solvers' work. Never weaken a statement, drop a case, or leave a lemma out because it looks unreachable today: a faithful hard target is also very valuable.

Faithfulness is also why read-backs exist: your own review is not independent — you know what the code is *supposed* to say. Delegate the read-back to a blind auditor and let the mathematician compare its testimony against the source.
