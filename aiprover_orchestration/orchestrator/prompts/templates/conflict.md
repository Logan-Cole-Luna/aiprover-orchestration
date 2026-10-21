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

<sketch_lemmas>
{lemmas}
</sketch_lemmas>

Conflict analysis. Several lemmas under one decision of your sketch are stuck: solvers and hand-backs have failed on them repeatedly, or one was refuted. The decision is the lemma `{decision_name}` and the way it was split into the lemmas below it.

<decision>
{decision_statement}
</decision>
<decision_proof>
{decision_proof}
</decision_proof>

<stuck_lemmas>
{stuck}
</stuck_lemmas>

{clauses}
Decide whether the stuck lemmas fail for one shared cause that lies in the decision, as a CDCL solver analyses a conflict to find the decision that caused it. Shared causes include: a statement or encoding with little Mathlib API (germs or ultrapowers, custom quotients, hand-rolled codings) where a standard formulation exists; a missing hypothesis that every child needs; a split that moved the difficulty instead of decomposing it. Unrelated failures of individual lemmas are not a shared cause.

Choose one action:
{backjump_action}- Local, when there is no shared cause: <local/>. The stuck lemmas continue with their own hand-backs.

With either action, close every stuck lemma whose proof you have found and checked with your tools (unit propagation): <leaf_proof name="the lemma's name">tactic proof of the lemma as stated (the text after `:= by`)</leaf_proof>, optionally with <leaf_helpers name="the lemma's name">helper declarations, each named `<the lemma's name>_...`</leaf_helpers>. Lean checks it against the lemmas listed before that lemma, and the lemma is proved at once.

{clause_output}Begin with <diagnosis>two or three sentences naming the cause</diagnosis>, then the clauses, then the tags of one action. Lean code goes inside the tags without Markdown fences.
