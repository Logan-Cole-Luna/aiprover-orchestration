Formalize the following result in Lean 4, building on the library below.

<library>
{library}
</library>

The library is fixed and verified: its definitions encode the setting of the result, and its theorems are proved. Use its definitions for every object they encode; do not restate, rename or redefine anything it declares. Its theorems may be used in the proof later; they are not part of the statement.

<informal_statement>
{informal_statement}
</informal_statement>

<informal_proof>
{informal_proof}
</informal_proof>
{feedback}
Produce:
- <definitions>: only the definitions the statement needs that the library lacks (often none). Only `inductive`, `structure`, `def`, `abbrev`, `namespace`/`end`, `open` and `notation` declarations; no theorems, no `sorry`, no `axiom`. Names must differ from the library's.
- <statement>: optional `open` lines, then exactly one declaration `theorem {theorem_name} <binders> : <type> := by sorry`, at top level (not inside a namespace).
- <notes>: one short paragraph mapping each part of the source statement to the Lean encoding, naming the library definitions used.

<definitions>
...
</definitions>
<statement>
...
</statement>
<notes>
...
</notes>