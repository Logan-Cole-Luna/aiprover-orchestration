# Run record

A run leaves the same result directory as a pipeline run, so the trace
pages (`orchestrator/trace_view`) render it.

| Path | Content |
|---|---|
| `results/<run>/trace.jsonl` | header line, then one line per step, appended as the run proceeds |
| `results/<run>/<slug>_standalone.lean` | the assembled file |
| `results/<run>/{Def,Thm,Sol,Check}_<M>.lean`, `module_build.log` | module statement check |
| `results/<run>/summary.json` | outcome |
| `results/<run>/informal/` | informalization (`informal_parts.json`, `.tex`, `.pdf`) |
| `results/<run>/trace.html`, `trace_replay.html` | rendered pages |
| `temp/<run>/` | working files: attempts, check reports, subagent prompts and replies; deleted at the close |

`Trace.from_lines` (`orchestrator/trace.py`) reads `trace.jsonl`: the first
line is the header; a line with `kind` is a step; a line without `kind`
updates the header. Steps are indexed in order and timed by their `time`
field. The file is only appended to.

## Recording functions (scripts/env.sh)

| Function | Records |
|---|---|
| `trace_start RUN PROBLEM.json SLUG MODEL` | header: problem, models, pipeline algorithm, Lean environment, role prompts, served config |
| `traced_check RUN STAGE LABEL FILE [opts]` | `lean_check` step from `aiprover check --json` |
| `traced_job RUN STAGE LEMMA JOB ROUND` | `model_call` step of the solver with the job's samples |
| `traced_subagent RUN STAGE LABEL ROLE MODEL PROMPT REPLY` | `model_call` step of a subagent |
| `trace_step RUN <jq options> FILTER` | any other step, in particular decisions |

Stages are `formalize`, `sketch`, `prove`, `assemble`, `review`, `informal`.

## Decisions

A decision is `trace_step "$RUN" ... '{kind: "decision", stage: ..., event: ..., ...}'`.
Pass Lean text and long text with `--rawfile name FILE` or `--arg name "..."`,
never by pasting it into the filter. Event names and fields follow the
pipeline:

| Event | Stage | Fields |
|---|---|---|
| `formalization_compiled` | formalize | `repair`, `definitions`, `preamble`, `statement`, `notes` (source-to-encoding mapping) |
| `formalization_rejected` | formalize | `definitions`, `statement`, `problems` |
| `audit_verdict` | formalize | `round`, `verdict` (FAITHFUL or REVISE), `issues`, `readback` |
| `sketch_accepted` | sketch | `repair`, `lemmas` (statements), `main_proof` |
| `sketch_rejected` | sketch | `repair`, `problems` |
| `aiprover_job_started` | prove | `lemma`, `statement`, `aiprover_job` |
| `aiprover_job_grown` | prove | `lemma`, `aiprover_job`, `sessions` |
| `lemma_proved` | prove | `lemma`, `worker` (sample index, or `"captain"`), `round`, `helpers`, `proof` |
| `solver_budget_exhausted` | prove | `lemma`, `aiprover_job`, `errors`, `attempts` |
| `lemma_handback` | prove | `lemma`, `action` (proof, split, restate, retry), `diagnosis`; `statement` and `previous_statement` for restate; `new_lemmas` for split |
| `replan` | sketch | `proved`, `failed` (lemma names) |
| `lemma_reused` | sketch | `lemma` |
| `final_verification` | assemble | `checks` (`references/gates.md`), `solution` (the standalone text) |
| `final_review` | review | `definitions_review`, `statement_review`, `proof_review`, `concerns`, `verdict`, `model` |
| `informal` | informal | `pdf`, `compiled`, `problems` |

Example, after the first audit:

```bash
. <skill>/scripts/env.sh
trace_step "$RUN" --rawfile readback "$ROOT/temp/$RUN/audit_0.reply.md" \
    --arg issues "none" \
    '{kind: "decision", stage: "formalize", event: "audit_verdict",
      round: 0, verdict: "FAITHFUL", issues: $issues, readback: $readback}'
```

## Close

The status is one of the pipeline's: `proved`, `review_flagged`,
`assembly_failed`, `lemmas_unproved`, `unfaithful_formalization`, `failed`.
Append the closing line, then write `summary.json`:

```bash
. <skill>/scripts/env.sh
jq -nc --arg status proved --arg finished "$(date +%Y-%m-%dT%H:%M:%S)" \
    '{outcome: {status: $status}, finished: $finished}' \
    >> "$ROOT/results/$RUN/trace.jsonl"
```

`summary.json` holds `run_id`, `uuid`, `slug`, `status`, `formalization`
(`definitions`, `preamble`, `statement`, `theorem_name`, `verdict`),
`sketch` (`main_proof`, `lemmas` with `name`, `statement`, `proved`,
`helpers`, `proof`), `checks`, `review` and `informal`, each when the run
reached it, and `wall_seconds`.
