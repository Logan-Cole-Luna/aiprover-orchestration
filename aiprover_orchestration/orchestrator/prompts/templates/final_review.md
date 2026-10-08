Referee the formal proof below against its source.

<informal_statement>
{informal_statement}
</informal_statement>

<informal_proof>
{informal_proof}
</informal_proof>

<target_statement>
{target_statement}
</target_statement>

<lean_solution>
{lean_solution}
</lean_solution>

Write each section in LaTeX text mode, ready to be typeset: mathematics in $...$ or \[...\], Lean identifiers as \leanname{{name}}, no Unicode mathematical symbols, no Markdown, no other macros or packages.

Reply with:
<definitions_review>
Each Lean definition and the source object it encodes; any discrepancy.
</definitions_review>
<statement_review>
The target theorem against the source statement: hypotheses, quantifiers, conclusion, edge cases; what must be supplied to recover the source's statement, if anything.
</statement_review>
<proof_review>
How the formal proof is organised, which step of the source proof each part carries out, and where it deviates from the source proof. Note any lemma whose statement is stronger or weaker than the step it represents.
</proof_review>
<concerns>
An itemize list of points a mathematician should know before relying on the result, or the single word None.
</concerns>
<verdict>FAITHFUL, UNFAITHFUL or UNCERTAIN</verdict>