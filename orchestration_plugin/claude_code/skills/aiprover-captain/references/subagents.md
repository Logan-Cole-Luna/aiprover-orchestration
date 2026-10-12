# Subagents

The auditor, the reviewer and the writer are subagents of this session
(Agent tool, `subagent_type: general-purpose`). Each prompt is assembled
from the repository's role texts, written to a file first, and passed to the
Agent tool verbatim; the reply is saved to a file. Both files are recorded
with `traced_subagent`. `P=$ROOT/aiprover_orchestration/orchestrator/prompts`.

Every prompt begins with the line

> Answer from the text of this message alone. Do not use any tools and do
> not read any files.

so that a blind role cannot reach the source through the repository.
`{lean_environment}` in a system prompt is replaced by the text of
`$P/system/lean_environment.md`. Placeholders `{name}` in a template are
filled with the values named; doubled braces `{{ }}` become single braces.

## Auditor (blind)

`model: sonnet`. A new subagent for every read-back.

```
<tool-use line>

<role>
{contents of $ROOT/aiprover_orchestration/roles/auditor.md}
</role>

{$P/templates/auditor.md with theorem_name = <slug> and
 lean_code = the definitions and the theorem statement, as compiled}
```

The prompt contains nothing else: no source statement, no source proof, no
notes. Files: `$T/audit_<k>.prompt.md`, `$T/audit_<k>.reply.md`; record with
`traced_subagent "$RUN" formalize audit<k> auditor sonnet <prompt> <reply>`.

You then judge the read-back as `$P/templates/judge_readback.md` asks and
record the verdict (`audit_verdict`).

## Reviewer (fresh)

`model: opus`. Spawned once on the verified file; again only if the file
changes.

```
<tool-use line>

<role>
{$P/system/reviewer.md with {lean_environment} replaced}
</role>

{$P/templates/final_review.md with informal_statement, informal_proof,
 target_statement = the target theorem (with definitions) as formalized,
 lean_solution = the standalone file}
```

The verdict is the text of `<verdict>`: FAITHFUL, UNFAITHFUL or UNCERTAIN.
Record with `traced_subagent "$RUN" review review reviewer opus ...` and a
`final_review` decision carrying the four review sections, `verdict` and `model`.

## Writer (fresh)

`model: opus`. Spawned once at the close of a run that has a sketch.

```
<tool-use line>

<role>
{$P/system/writer.md with {lean_environment} replaced}
</role>

{$P/templates/informal.md with informal_statement, informal_proof,
 lean_source = the standalone file, or the sketch with the proofs found and
               `sorry  -- not verified` for the others,
 lemmas = one line per lemma in sketch order: `<name> (verified)` or
          `<name> (not verified)`,
 theorem_name = <slug>}
```

Typeset the reply:

```bash
. <skill>/scripts/env.sh
informal_build "$RUN" <slug> <lean source file> "$T/lemmas.json" opus "$T/informal.reply.md" <true|false>
```

`$T/lemmas.json` is `[["name", true], ...]` in sketch order; the last
argument is `true` when the Lean source is the verified file.
`informal_build` prints `contract: ok` and `compiled`, or the contract
problems and LaTeX errors. On problems, send them to a new writer subagent
together with its previous reply, at most twice. Record the call with
`traced_subagent "$RUN" informal informal writer opus ...` and an `informal`
decision with `pdf` (the path printed under `results/<run>/informal/`) and
`compiled`.
