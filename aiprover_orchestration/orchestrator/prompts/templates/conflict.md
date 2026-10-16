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

<learned_clauses>
{clauses}
</learned_clauses>

Decide whether the stuck lemmas fail for one shared cause that lies in the decision, as a CDCL solver analyses a conflict to find the decision that caused it. Shared causes include: a statement or encoding with little Mathlib API (germs or ultrapowers, custom quotients, hand-rolled codings) where a standard formulation exists; a missing hypothesis that every child needs; a split that moved the difficulty instead of decomposing it. Unrelated failures of individual lemmas are not a shared cause.

Choose one action:
1. Backjump, when the cause lies in the decision: <backjump>theorem {decision_name} <binders> : <type> := by sorry</backjump>, the decision restated so that the cause is removed (for example in standard Mathlib vocabulary). All lemmas below `{decision_name}` are retracted; `{decision_name}` is reopened with the new statement and handed to solvers, and proofs of retracted lemmas are reused if their statements return. The main proof and the proofs that use `{decision_name}` must still compile with the new statement.
2. Local, when there is no shared cause: <local/>. The stuck lemmas continue with their own hand-backs.

With either action, record what this conflict taught as learned clauses, one per <clause>fact</clause> tag: facts that hold for the rest of the run, such as why the encoding fails, which Mathlib declarations are absent, or which route should not be tried again. They are given to every later solver session and to your later decisions.

Begin with <diagnosis>two or three sentences naming the cause</diagnosis>, then the clauses, then the tags of one action. Lean code goes inside the tags without Markdown fences.
