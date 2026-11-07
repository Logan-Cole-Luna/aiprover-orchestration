Find a proof of the theorem below. No proof is given: the proof you write is the plan that the next stages decompose into lemmas and prove in Lean, so it must be correct and complete at the level of a careful mathematical paper.

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
- Consider more than one approach, and choose the one whose steps are most likely to hold and to be provable from Mathlib and the library. Prefer results that already exist over proving them again.
- Test doubtful steps on small cases, and check with Lean the claims you rely on where you can: `by plausible` searches for a counterexample, and `by decide` settles finite cases.
- State every intermediate claim precisely, with its hypotheses, so that it can become a lemma.
- Never assume an unproved claim. If you cannot complete a step, say so plainly in the proof, and name the gap.

Reply with:
<approach>Two to four sentences: the approaches considered, and why this one.</approach>
<proof>The complete informal proof, in numbered steps, each a claim with its argument.</proof>
