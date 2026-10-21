Below is the task a Lean prover receives for the lemma `{lemma_name}`: the informal statement with the Lean setting, the fixed Lean statement, and the source's informal proof of the main result. Write the informal proof of the lemma `{lemma_name}` that you, as captain, give the prover as its direction.

Requirements:
- Numbered steps that a Lean proof can follow in order, in plain mathematical language.
- Name in each step the hypotheses, Setting definitions and earlier lemmas it uses, and the Mathlib declarations you expect it to need (exact names only when you are confident they exist). Earlier lemmas are proved: a step that needs one cites it by name rather than proving it again.
- Point out the key idea or the step most likely to be hard.
- At most 15 lines. No Lean code.

Reply with the informal proof only.

<informal_theorem>
{theorem_text}
</informal_theorem>

Setting (FIXED Lean 4 code):
```lean
{context}
```

The Lean 4 statement of the lemma is FIXED:
```lean
{statement}
```

<informal_proof>
{informal_proof}
</informal_proof>
