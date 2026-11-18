

The statement may be false. If your experiments find a counterexample, check it and report it in full. If you prove the negation in Lean, write `refutation.lean` in the current directory in the same form as `proof.lean`, ending with

{negation} := by
  ...

The run checks it the same way and, if it holds, ends with the statement disproved.
