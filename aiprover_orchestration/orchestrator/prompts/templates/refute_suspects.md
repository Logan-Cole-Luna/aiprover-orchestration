A reviewer suspects that the lemmas named below are false. Test each one: look for a counterexample, check it with Lean, and if you find one, prove the negation of the lemma. Do not weaken or restate a lemma; the question is only whether it is false as stated.

<definitions>
{definitions}
</definitions>

<lemmas>
{lemmas}
</lemmas>

Suspected lemmas: {suspects}

<reasons>
{reasons}
</reasons>

For each lemma you refute, reply with
<refutation name="lemma_name">tactic proof of `¬ (type_of% @lemma_name)` (the text after `:= by`)</refutation>
and, only where needed, helper declarations, each named `lemma_name_...`:
<helpers name="lemma_name">declarations</helpers>
A refutation counts only if Lean accepts it with the standard axioms. Give nothing for a lemma you could not refute.
