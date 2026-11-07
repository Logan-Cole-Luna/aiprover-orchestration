Judge the proof sketch below before solver agents spend time on its lemmas. The sketch compiles: its main proof derives the theorem from the lemmas, each left as `sorry`. Each lemma will be proved by an independent solver agent that sees the definitions, the lemma, and the statements of the lemmas before it.

Blocking issues, each a reason for REVISE:
- A lemma is equivalent to the theorem or stronger than it, so that the main proof only unpacks that lemma and the difficulty is moved rather than decomposed.
- A lemma is a known open problem while the theorem is not known to depend on it.
- A Lean statement does not say what the proof needs: a missing hypothesis, a wrong quantifier, or a conclusion too weak for its use.
- The main proof misses a case of the argument.

Advisory notes, which never cause REVISE: a lemma that bundles several steps, a gap that could be split further, a lemma that nothing uses, an encoding with little Mathlib support, and similar improvements. Later stages split and restate lemmas when solvers get stuck, so list these as notes.

A lemma you believe is false is not judged here: name it under <suspect>, with your reason under <issues>. It is tested at once (a counterexample search and an attempt to prove its negation), and only a confirmed refutation rejects the sketch.

When no proof of the theorem is known, the sketch must still commit to a concrete approach in which every lemma is strictly simpler than the theorem; conjectural lemmas are acceptable if each is weaker than the theorem.
{previous}
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
<verdict>ACCEPT, or REVISE only if a blocking issue remains</verdict>
<issues>
If REVISE: each blocking issue, naming the lemma, and how to fix it. Otherwise: none.
</issues>
<notes>Advisory notes, or none.</notes>
<suspect>Names of lemmas you believe are false, separated by spaces, or none.</suspect>
