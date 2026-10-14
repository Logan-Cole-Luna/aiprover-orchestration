Write a proof sketch for the theorem below. The sketch consists of lemmas, each stated in full and left as `sorry`, plus a proof of the main theorem from those lemmas. Each lemma will be proved independently by a solver agent that sees the definitions, the lemma, and the statements of the lemmas before it.

Guidelines:
- Theorems already proved in the definitions (a library of verified results) may be used directly in lemmas and in the main proof; do not restate them as lemmas.
- Put every nontrivial step, in particular every induction over a derivation or over syntax, into its own lemma. Keep lemmas small and self-contained; a solver sees only earlier lemmas.
- The main proof should be a short combination of the lemmas and the definitions' constructors. It must not use `sorry`.
- Lemma names must be unique and must not be `solution`.
- Theorems named `setting_...` at the end of the definitions are facts about the setting, proved before planning. Use them, and check the theorem against them before you plan a proof.

<definitions>
{definitions}
</definitions>

<preamble>
{preamble}
</preamble>

<theorem>
theorem solution{signature} := by
  sorry
</theorem>

<informal_proof>
{informal_proof}
</informal_proof>
{feedback}
Reply with:
<lemmas>
theorem lemma_name <binders> : <type> := by sorry
...
</lemmas>
<main_proof>
tactic proof of `solution` (the text after `:= by`)
</main_proof>

If the theorem as stated is false, do not sketch a proof. Reply instead with <refutation>tactic proof of `¬ (the statement of solution)` (the text after `:= by`)</refutation>, optionally with <helpers>helper declarations, each named `solution_...`</helpers>. It is compiled as `theorem solution_refuted : ¬ (type_of% @solution)` against the definitions alone and counts only if Lean accepts it with the standard axioms; a refuted theorem ends the run as disproved. Suspecting that the theorem is false is not a refutation.