<definitions>
{definitions}
</definitions>

<preamble>
{preamble}
</preamble>

<informal_proof>
{informal_proof}
</informal_proof>

<main_proof>
{main_proof}
</main_proof>

Solver agents have failed {attempts} times to prove the lemma `{lemma_name}` of your proof sketch. The other lemmas are still being proved. Diagnose the failure from the attempts below and choose one action.

<sketch_lemmas>
{lemmas}
</sketch_lemmas>

<failed_lemma>
{failed}
</failed_lemma>

<carried_knowledge>
{knowledge}
</carried_knowledge>
The carried knowledge is given to every new solver session on this lemma: what earlier sessions searched, read, reasoned and wrote.
{clauses}
Actions:
1. Prove it yourself, when the attempts show the missing step or the lemma is routine, so that the proof is a short fix of the solvers' code or a routine argument; a proof that needs extended work is split instead (action 2): <proof>tactic proof of `{lemma_name}` as stated (the text after `:= by`)</proof>, optionally with <helpers>helper declarations, each named `{lemma_name}_...`</helpers>. The proof may use the lemmas listed before `{lemma_name}`.
2. Split it, when one step is hard on its own: <split>new lemma statements `theorem name <binders> : <type> := by sorry`, one per declaration, with new names</split> and <proof>a proof of `{lemma_name}` from them and the earlier lemmas</proof>. The new lemmas are placed before `{lemma_name}` and handed to solvers. When the lemma asks for an object to be constructed (the witness of an `∃`, such as a function and its properties), define the object first and split its properties into separate lemmas about it: <split_definitions>`def` declarations, each named `{lemma_name}_...`, taking as parameters the variables they depend on and using only the definitions, Mathlib and earlier definitions</split_definitions>. They are placed before the new lemmas, which may use them; the proof then supplies the defined object as the witness.
3. Restate it, when the statement is false or lacks a hypothesis that the main proof can supply: <restate>theorem {lemma_name} <binders> : <type> := by sorry</restate>. The main proof must still compile with the new statement; lemmas whose proofs use `{lemma_name}` are not restated.
4. Retry, when the failures come from the solver setup (time limits, sessions without an answer) or the solvers are heading in a wrong direction that revised knowledge can correct: <retry/>. A retry gives the lemma further attempts even when it has used all of them. When the sessions left only searches and no code, judge whether a fact they looked for is absent from Mathlib; if so, split it off as a lemma of its own rather than retrying.
5. Refute it, when the statement is false and no added hypothesis that the main proof can supply would repair it: <refute>tactic proof of `¬ (the statement of {lemma_name})` (the text after `:= by`)</refute>, optionally with <helpers>helper declarations, each named `{lemma_name}_...`</helpers>. It is compiled as `theorem {lemma_name}_refuted : ¬ (type_of% @{lemma_name})` against the definitions alone, without the other lemmas, and counts only if Lean accepts it with the standard axioms. A refuted lemma is never attempted again: no new jobs start, and you revise the sketch with the refutation in hand. Suspecting that a lemma is false is not a refutation; test a small case first, and restate (action 3) when a hypothesis is merely missing.

Do not attempt a long or complex proof yourself. When the lemma bundles several facts (a conjunction, or an existential with many conditions), or its proof would take extended Lean work, split it so that the solvers prove the parts. This reply has a spend and turn limit; a reply that reaches it is discarded, and the hand-back continues without action 1.

A statement in an encoding with little Mathlib API, such as germs `Filter.Germ l β` over `Filter.hyperfilter`, is better split or restated in terms of ordinary functions: state its parts as `∀ᶠ n in l, …` (`Filter.Eventually`, which has broad Mathlib API) and connect them to germs with `Filter.Germ.coe_eq`, `Filter.Germ.liftPred_coe` and `Filter.Germ.liftRel_coe`.

With any action, you may replace the carried knowledge with <knowledge>notes for the next sessions</knowledge>: keep the findings that help, remove directions you judge wrong, and add the approach or Mathlib declarations you expect to work. An empty <knowledge></knowledge> clears it; without the tag it is kept.

{clause_output}{retry_note}Begin with <diagnosis>one or two sentences</diagnosis>, then the tags of one action. Lean code goes inside the tags without Markdown fences.