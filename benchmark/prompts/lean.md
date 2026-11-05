Formalize and prove the following result in Lean 4 with Mathlib.

Environment: the current directory is a Lake project pinned to Lean v4.23.0 and
Mathlib v4.23.0 (prebuilt). Check a file with `lake env lean <file>`. Do not run
`lake update` or rebuild Mathlib. Work only inside the current directory; do
not read files outside it and do not use the network.

Task:
1. Write the final result to `Solution.lean`, which begins with
   `import Mathlib`.
2. State the theorem faithfully; do not weaken it. Define in `Solution.lean`
   every object of the statement in full, exactly as the statement specifies;
   do not substitute a simpler system.
3. Prove it. The file must compile with no `sorry`, no `admit`, no new
   `axiom`, and `#print axioms` on the final theorem must list only
   `propext`, `Classical.choice` and `Quot.sound`.
4. End the file with `#print axioms` for the final theorem.

You are finished when `lake env lean Solution.lean` succeeds under these
conditions.

## Informal statement

{informal_statement}

## Informal proof

{informal_proof}
