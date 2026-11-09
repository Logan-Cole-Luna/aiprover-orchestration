Prove as many of the pending lemmas below as you can with short proofs, before they go to a prover that takes hours each. Spend your effort on lemmas that a few lines settle: definitional unfoldings, finite computations, direct applications of library or Mathlib results. Skip any lemma that needs a long argument; it will be proved elsewhere.

Check each proof with Lean before you give it. A proof may use the lemmas listed before its lemma, as stated, and the definitions and library.

<definitions>
{definitions}
</definitions>

<preamble>
{preamble}
</preamble>

<lemmas>
{lemmas}
</lemmas>

Pending lemmas: {pending}

Reply with one block per lemma you proved, and nothing for the others:
<proof name="lemma_name">tactic proof (the text after `:= by`)</proof>
and, only where needed, helper declarations, each named `lemma_name_...`:
<helpers name="lemma_name">declarations</helpers>
