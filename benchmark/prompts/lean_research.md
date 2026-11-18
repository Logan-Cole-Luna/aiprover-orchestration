Prove the following result in Lean 4 with Mathlib.

Environment: the current directory is a Lake project pinned to Lean v4.23.0 and
Mathlib v4.23.0 (prebuilt). Reference material lies in `source/`. The shell
accepts only these commands: `lake env lean <file>` (whose output may be piped
into `head`, `tail`, or `grep`) and `python3` (scripts or `-c`). Other commands,
such as `ls`, `cat`, or `lake --version`, are refused; use the Read, Write, Edit,
Glob, and Grep tools for files. Do not run `lake update` or rebuild Mathlib.
Search arXiv mathematics abstracts with `python3 tools/paper_search.py "query"`,
and read a paper with `python3 tools/paper_search.py --read <arXiv id>`.

The statement may have no published proof. Work on it until it is proved or the
budget is spent; do not stop early.

Task:
1. Write the final result to `Solution.lean`, which begins with
   `import Mathlib`, then the definitions below exactly as given, then your
   proof, ending with the theorem below, stated exactly as given.
2. Do not change, weaken, or redefine the statement or its definitions.
3. The file must compile with no `sorry`, no `admit`, no new `axiom`, and
   `#print axioms` on the final theorem must list only `propext`,
   `Classical.choice` and `Quot.sound`.
4. End the file with `#print axioms` for the final theorem.

You are finished when `lake env lean Solution.lean` succeeds under these
conditions.

## Informal statement

{informal_statement}

## Formal statement

{formal_statement}

## Informal proof

{informal_proof}
