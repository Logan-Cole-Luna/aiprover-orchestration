Before any proof is planned, test the setting of the theorem below. A statement proved in a setting that is contradictory or degenerate is vacuous, and effort spent proving a false statement is lost. Look for:
- hypotheses of the theorem that contradict each other or the definitions;
- a defined structure that is degenerate: a presented group or quotient in which generators coincide or become trivial, an empty or one-element type, a constant forced to one value;
- a small or extreme instance (the trivial group, n = 0 or 1, an empty index set) in which the theorem's conclusion fails or holds for a trivial reason.

A fact counts only if Lean accepts it. State each fact you can prove as a complete theorem named `setting_...`, with its proof and no `sorry`; it is compiled against the definitions and then added to them, so every later proof may use it. Do not attempt the theorem itself here, and do not state facts you cannot prove.

<definitions>
{definitions}
</definitions>

<preamble>
{preamble}
</preamble>

<theorem>
theorem solution{signature} := by
  sorry
</theorem>

<informal_proof>
{informal_proof}
</informal_proof>
{feedback}
Reply with <diagnosis>what you tested and found, in a few sentences</diagnosis>, then either
<facts>
theorem setting_name <binders> : <type> := by
  <proof>
...
</facts>
or, when you find nothing to prove about the setting, <consistent/>. Lean code goes inside the tags without Markdown fences.
