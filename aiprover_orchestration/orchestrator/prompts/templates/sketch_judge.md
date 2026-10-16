Judge the quality of the proof sketch below before solver agents spend time on its lemmas. The sketch compiles: its main proof derives the theorem from the lemmas, each left as `sorry`. Each lemma will be proved by an independent solver agent that sees the definitions, the lemma, and the statements of the lemmas before it.

Reject the sketch (REVISE) if any of the following holds:
- A lemma is equivalent to the theorem or stronger than it, so that the main proof only unpacks that lemma and the difficulty is moved rather than decomposed.
- A lemma is a known open problem, or is false, while the theorem is not known to depend on it.
- A lemma is not self-contained: it cannot be understood or proved from the definitions and the earlier lemmas alone.
- A lemma bundles several independent nontrivial steps that should be separate lemmas.
- A lemma is stated in an encoding that Mathlib barely supports (germs or ultrapowers, custom quotients, hand-rolled codings) where an equivalent statement in standard, well-supported vocabulary exists.

When no proof of the theorem is known, the sketch must still commit to a concrete approach in which every lemma is strictly simpler than the theorem; conjectural lemmas are acceptable if each is weaker than the theorem.

<theorem>
theorem solution{signature} := by
  sorry
</theorem>

<informal_proof>
{informal_proof}
</informal_proof>

<lemmas>
{lemmas}
</lemmas>

<main_proof>
{main_proof}
</main_proof>

Reply with:
<verdict>ACCEPT or REVISE</verdict>
<issues>
If REVISE: each problem, naming the lemma, and how to restructure the sketch. If ACCEPT: none.
</issues>
