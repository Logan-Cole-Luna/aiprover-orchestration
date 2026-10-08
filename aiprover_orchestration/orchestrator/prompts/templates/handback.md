Solver agents have failed {attempts} times to prove the lemma `{lemma_name}` of your proof sketch. The other lemmas are still being proved. Diagnose the failure from the attempts below and choose one action.

<definitions>
{definitions}
</definitions>

<preamble>
{preamble}
</preamble>

<sketch_lemmas>
{lemmas}
</sketch_lemmas>
<main_proof>
{main_proof}
</main_proof>

<informal_proof>
{informal_proof}
</informal_proof>

<failed_lemma>
{failed}
</failed_lemma>

Actions:
1. Prove it yourself, when the attempts show the missing step or the lemma is routine: <proof>tactic proof of `{lemma_name}` as stated (the text after `:= by`)</proof>, optionally with <helpers>helper declarations, each named `{lemma_name}_...`</helpers>. The proof may use the lemmas listed before `{lemma_name}`.
2. Split it, when one step is hard on its own: <split>new lemma statements `theorem name <binders> : <type> := by sorry`, one per declaration, with new names</split> and <proof>a proof of `{lemma_name}` from them and the earlier lemmas</proof>. The new lemmas are placed before `{lemma_name}` and handed to solvers.
3. Restate it, when the statement is false or lacks a hypothesis that the main proof can supply: <restate>theorem {lemma_name} <binders> : <type> := by sorry</restate>. The main proof must still compile with the new statement; lemmas whose proofs use `{lemma_name}` are not restated.
4. Retry unchanged, when the failures come from the solver setup (time limits, sessions without an answer) rather than the lemma: <retry/>.

Begin with <diagnosis>one or two sentences</diagnosis>, then the tags of one action. Lean code goes inside the tags without Markdown fences.