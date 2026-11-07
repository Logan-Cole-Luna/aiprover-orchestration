Find a proof of the theorem below. No proof is given. Write {count} genuinely different approaches, each a complete plan at the level of a careful mathematical paper, so that the most promising can be chosen and the others kept as fallbacks.

<informal_statement>
{informal_statement}
</informal_statement>

<library>
{library}
</library>

<definitions>
{definitions}
</definitions>

<lean_statement>
{statement}
</lean_statement>

Work as follows:
- Prefer results that already exist in the library and Mathlib over proving them again.
- Test doubtful steps on small cases, and check with Lean the claims you rely on where you can: `by plausible` searches for a counterexample, and `by decide` settles finite cases.
- State every intermediate claim precisely, with its hypotheses, so that it can become a lemma.
- Never assume an unproved claim. Where an approach has a step you cannot complete, name it as a gap, state it precisely, and say why it is open. A gap must be strictly weaker than the theorem; make it as narrow as the approach allows.

Reply with one block per approach:
<approach name="1">
<summary>Two to four sentences: the idea, and why it may work.</summary>
<proof>The informal proof, in numbered steps, each a claim with its argument.</proof>
<gaps>Each open step: its precise statement, why it is open, and any evidence for it (small cases checked, related known results). "None" if the proof is complete.</gaps>
</approach>
