# Final result

Adapted from the faithfulness and proof-integrity rubrics of autoform-bot (`skills/agent-review/references/`, MIT license, https://github.com/facebookresearch/autoform-bot).

The unit is the verified result of a run: its target theorem and the proof Lean accepted. The two axes are scored separately and never averaged: a sorry-free proof of a weakened statement still fails faithfulness.

## Evidence, checked first

The final verification is mechanical: compilation, absence of `sorry`, the axioms used, and whether the solution's type equals the target theorem's type. A failed check caps the proof axis at 1. The grader then reads the source statement and the target statement with its definitions. The proof is judged from statistics of every lemma proof (lines, automation calls, unused helpers) and from the text of the two longest proofs, since each lemma proof is graded separately by the lemma-proof rubric; the evidence therefore keeps one size whatever the length of the proof. The reviewer's verdict is recorded beside the grade and is not evidence.

## Axes

Faithfulness: as in the faithfulness rubric, applied to the target statement and the definitions it depends on, including definitions the run added or a library supplied.

Proof: integrity and quality of the proof as a whole. 5 genuine, standard axioms only, lemmas of reasonable size and reused where possible; 4 genuine with minor redundancy, such as a few lemma proofs much longer than their statements need; 3 genuine but hard to maintain, such as long proofs across most lemmas or unused helpers in several, or diverging from the followed proof without reason; 2 genuine but relying on an auxiliary definition or hypothesis whose connection to the source is unproved; 1 the verification fails or a gap remains; 0 vacuous or circular.

Hard ceilings as in the faithfulness rubric for the first axis; any `sorry` or nonstandard axiom caps the proof axis at 1. The result passes when faithfulness is at least 4 and proof at least 3.
