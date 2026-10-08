Write a proof sketch for the theorem below. The sketch consists of lemmas, each stated in full and left as `sorry`, plus a proof of the main theorem from those lemmas. Each lemma will be proved independently by a solver agent that sees the definitions, the lemma, and the statements of the lemmas before it.

Guidelines:
- Theorems already proved in the definitions (a library of verified results) may be used directly in lemmas and in the main proof; do not restate them as lemmas.
- Put every nontrivial step, in particular every induction over a derivation or over syntax, into its own lemma. Keep lemmas small and self-contained; a solver sees only earlier lemmas.
- The main proof should be a short combination of the lemmas and the definitions' constructors. It must not use `sorry`.
- Lemma names must be unique and must not be `solution`.

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