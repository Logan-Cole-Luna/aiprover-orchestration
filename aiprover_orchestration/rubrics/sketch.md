# Proof sketch

Adapted from the roadmap-quality rubric of autoform-bot (`skills/agent-review/references/roadmap-quality.md`, MIT license, https://github.com/facebookresearch/autoform-bot).

The unit is one sketch the orchestrator produced: lemma statements left as `sorry` and a main proof of the theorem from them. The followed proof is the source proof in autoformalization mode and the orchestrator's own draft in prove mode; the sketch is graded against that proof.

## Evidence, checked first

1. Lean settles whether the sketch compiled; a compile error recorded in the trace is mechanical and is quoted, not judged. A sketch that did not compile scores at most 1 on encoding.
2. The trace records each lemma's fate later in the run (proved by a prover, proved by the orchestrator, split, restated, refuted, never proved). A refuted lemma is false; a lemma proved as stated is true.
3. The grader reads the followed proof, every lemma statement and the main proof, and maps each lemma to the step it carries out.
4. Whether a lemma is a declared gap is mechanical: the evidence `declared_gaps` lists the lemmas whose names carry the pipeline's gap prefix (`gap_`). Every other lemma is an ordinary lemma, whatever its comments call it; a lemma described as a conjecture, an open case or an assumption without that prefix is an ordinary lemma with no known proof.
5. The judge's verdict is another model's opinion and is not evidence.

## Axes, scored separately

Fidelity: the sketch carries out the followed proof. 5 every step maps to a lemma or a line of the main proof, in the proof's order and strength; 4 minor reordering or merging; 3 a step replaced by a different but valid route; 2 an essential step missing, weakened or replaced by an unproved assumption; 1 the sketch follows another argument altogether without reason; 0 no relation.

Decomposition: one result per lemma, every dependency stated and used, granularity a prover can handle, lemmas true. 5 each lemma is a single result of prover size, every lemma is used and every needed fact is a lemma or in Mathlib; 4 one lemma slightly coarse or one unused lemma; 3 a lemma that bundles several nontrivial steps or a missing intermediate; 2 a lemma that is false, as hard as the theorem, or an open problem not declared as a gap; 1 the main difficulty sits in the leaf lemmas, so that the theorem is moved without being decomposed; 0 no decomposition.

Encoding: the Lean statements say what the proof needs, in vocabulary Mathlib supports. 5 precise and idiomatic; 4 minor awkwardness; 3 an encoding with little Mathlib support or a hypothesis stronger than needed; 2 a statement that does not say what its use requires (missing hypothesis, wrong quantifier, conclusion too weak); 1 several such or compile failures; 0 unusable.

A sketch relocates the theorem instead of reducing it when its leaf lemmas without a known proof together carry the whole difficulty of the theorem: the main proof and the lemmas that have an argument only assemble the theorem from those leaves (a case split, a conjunction, a restatement) and settle no part of its open content. A sketch whose open leaves are strictly narrower than that content, because its proved or provable lemmas settle part of it, does not relocate the theorem.

In prove mode a draft may itself be a reduction whose declared gaps carry the difficulty. A sketch that declares exactly the draft's gaps, as `gap_` lemmas of the same strength, follows its draft and does not relocate the theorem: the reduction is the draft's, graded by the completeness of the draft rubric. Relocation concerns open content that the draft proves or does not mark as a gap, and the draft's gaps restated as ordinary lemmas.

Hard ceilings: a refuted lemma caps decomposition at 2; a sketch that did not compile caps encoding at 1; a sketch that relocates the theorem scores 1 on decomposition and, in prove mode, at most 3 on fidelity, however its leaf lemmas are named or described (ordinary lemmas, lemmas called conjectures). Apart from relocation, in prove mode a declared gap is no defect of decomposition, a step labelled as a gap that the draft proves caps fidelity at 3, and a lemma with no known proof that is not a declared gap caps decomposition at 2. A sketch passes when every axis is at least 3; it is rejected when fidelity or decomposition is 2 or below.
