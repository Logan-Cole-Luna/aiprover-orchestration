# Drafted proof (prove mode)

Adapted from the roadmap-quality and proof-integrity rubrics of autoform-bot (`skills/agent-review/references/`, MIT license, https://github.com/facebookresearch/autoform-bot).

The unit is one informal proof the orchestrator drafted when no proof was given (`draft.md`, or one `approach_N.md` when several approaches were drafted). The draft is the mathematical strategy of the run: every later stage executes it. This rubric therefore grades strategy only; failures of execution (provers, Lean encoding, budget) belong to the lemma-failure rubric.

## Evidence, checked first

1. The theorem statement, the source notes and the library results the draft cites are read in full.
2. Mechanical facts recorded by the run are taken as given: which draft the sketch followed, which of its steps became lemmas, which lemmas were proved, refuted or left open, and the run's outcome. A step whose lemma was refuted in Lean is false.
3. Each step marked as proved, known or routine is checked for an actual argument or an exact citation; a step labelled as a gap or a case left open is a gap, however it is worded.
4. When the statement is an open problem, the grader states so and judges whether the draft claims more than it supplies.

## Axes, scored separately

Correctness: every asserted step is true as written and follows from what precedes it. 5 no error; 4 a minor slip that a local fix repairs; 3 an error confined to one step whose repair is evident; 2 a false step on which the argument depends; 1 several false steps; 0 the argument is wrong throughout or proves another statement.

Completeness: the draft gives an argument for every case, judged by the share of the difficulty that its gaps carry and not by their number. 5 complete; 4 only routine verifications left to the reader; 3 one nontrivial step asserted without argument; 2 a gap confined to a case or step that is clearly smaller than the theorem; 1 gaps that together carry the main difficulty, so the draft is a reduction; 0 no argument beyond restating the goal.

Approach: the choice of strategy for this statement. 5 a sound strategy that reuses the library and keeps each step within reach of a prover; 4 sound with avoidable detours; 3 workable but materially harder than necessary, or needing infrastructure Mathlib lacks; 2 the remaining difficulty is concentrated in steps as hard as the theorem; 1 the approach cannot work as stated; 0 no approach.

Hard ceilings: a step that Lean refuted caps correctness at 2; an explicit gap caps completeness at 2, and a gap that carries the main difficulty caps it at 1; a draft whose gaps carry the whole difficulty of an open statement caps approach at 2. A draft passes when correctness is at least 4 and completeness at least 3; it is rejected when correctness is 2 or below. A draft with gaps is never described as a proof.
