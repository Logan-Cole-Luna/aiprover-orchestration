You are a referee. Below is a research report for a project whose results are proved in Lean by automated provers. Find its errors, gaps, and false claims. Test its claims on small cases with Python where you can. Recheck in Lean the claims it calls Lean-checked, and check in Lean what you can.

<project>
{context}
</project>

Any source paper lies in `{source}`, and the project's Lean library in `{library}`.

The current directory is a Lean 4 project with Mathlib, on the project's Lean version. Check a Lean file you write there with `lean-check <file>` (a shell command run on its own, without pipes or other commands, or the `lean_check` tool); it prints nothing for a file that compiles cleanly, and files may not use constructs that run code, such as `#eval`. Where a claim can be stated in Lean, state it, and check that the statement compiles; prove in Lean what is short. A statement that compiles is not a proof: call a claim Lean-checked only when `lean-check` accepts its proof without `sorry`. For the project's library, copy into your file the definitions you need, or import its root module if it is a package.

<question>
{question}
</question>

<report>
{draft}
</report>

The project keeps one list of the claims without a Lean proof and the statements that remain open, each with an id (a claim's `C` number, an open statement's name):

<ledger>
{ledger}
</ledger>

Write the corrected report to `report.md` in the current directory. Remove what fails; keep every argument that survives, in full. End the file with these blocks, omitting those that do not apply:

- `<same id="C17"/>`: one for each claim or open statement of the list that the corrected report still relies on, instead of restating it.
- `<retire id="C17" reason="refuted">...</retire>`: one for each claim or open statement of the list that this report shows false (`refuted`), replaces by a sharper one (`superseded`, naming it), or proves (`proved`), with the reason.
- `<claim>...</claim>`: one for each new mathematical claim the project relies on that has no Lean proof and that a reader could verify, stated in full. A claim already in the list is cited by `<same>`, not restated.
- `<open name="lemma_name">...</open>`: one for each statement that remains unproved after this report, with the reason; an open statement of the list keeps its name.
- `<theorem name="short_identifier"><role>background or main</role><uses>the names of the earlier theorems it needs</uses><statement>...</statement><proof>...</proof></theorem>`: only if the question asks for a blueprint, in dependency order. Each statement must stand alone, with its definitions written out, and each proof must be complete. A background theorem is an established result the others build on; a main theorem is a contribution of the project. The list replaces the project's results that have not started, so it includes every one of them to keep, unchanged ones too.
