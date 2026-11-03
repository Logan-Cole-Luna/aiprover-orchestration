You are a referee. Below is a research report for a project whose results are proved in Lean by automated provers. Find its errors, gaps, and false claims. Test its claims on small cases with Python where you can.

<project>
{context}
</project>

Any source paper lies in `{source}`, and the project's Lean library in `{library}`.

<question>
{question}
</question>

<report>
{draft}
</report>

Write the corrected report to `report.md` in the current directory. Remove what fails; keep every argument that survives, in full. End the file with these blocks, omitting those that do not apply:

- `<claim>...</claim>`: one for each mathematical claim the project relies on that has no Lean proof and that a reader could verify, stated in full.
- `<open name="lemma_name">...</open>`: one for each statement that remains unproved after this report, with the reason.
- `<milestone name="short_identifier"><role>background or main</role><uses>the names of the earlier milestones it needs</uses><statement>...</statement><proof>...</proof></milestone>`: only if the question asks for milestones, in dependency order. Each statement must stand alone, with its definitions written out, and each proof must be complete. A background milestone is an established result the others build on; a main milestone is a contribution of the project.
