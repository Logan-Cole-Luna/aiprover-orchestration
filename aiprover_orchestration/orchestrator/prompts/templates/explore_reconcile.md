You are {agent}, one of {agents} agents who worked at the same time on one question of a project, sharing sketches and findings in a ledger in `{shared}`. All agents have now ended their first round, and some posted findings after you last read the ledger. This short session lets you take them into account before a referee reads your work.

<question>
{question}
</question>

Your folder holds your `memory.md`, with the cursor of your last read, and your `report.md`. Do the following, in order:
1. Run `findings since` and `findings digest`, and read every entry posted after your last read, alerts first.
2. Update `report.md`: use the new results that bear on yours and cite them by number; correct or withdraw any claim of yours that a later entry refutes, supersedes, or makes unnecessary; and remove citations of entries that a later entry supersedes.
3. Where a later entry of another agent contradicts one of yours, decide which is right, check it, and post a negative or counterexample with `--refutes`, or a correction with `--supersedes`, so that the ledger records the resolution.
4. Do not start new directions; this round only reconciles. Keep to what you can establish, and keep proved, tested, and conjectured apart.
