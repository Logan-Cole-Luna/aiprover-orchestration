# Shell environment of the aiprover-captain skill. Shell state does not
# persist between commands, so every command sources this file first:
#     . <skill directory>/scripts/env.sh
# It defines the repository locations and the functions that record a run in
# results/<run>/trace.jsonl. Nothing here calls a model.

ROOT=$(readlink -f ~/.config/aiprover_orchestration/root)
# The AIProver configuration (model endpoint, paths): $AIPROVER_CONFIG when set,
# else the plugin's aiprover.toml as linked by setup.sh.
export AIPROVER_CONFIG=${AIPROVER_CONFIG:-$(readlink -f ~/.config/aiprover_orchestration/aiprover.toml)}
AIP=$ROOT/AIProver/AIProver_plugin/bin/aiprover
WS=$ROOT/orchestration_workspace
JOBS=$("$AIP" config --shell | sed -n 's/^AIP_WORK_ROOT=//p')/jobs

# Creates results/<run>/ and temp/<run>/ and writes the trace header.
# Arguments: run id, problem row (JSON file), theorem name, captain model.
trace_start() {
    local run=$1 problem=$2 theorem=$3 captain=$4
    mkdir -p "$ROOT/results/$run" "$ROOT/temp/$run"
    (cd "$ROOT" && python3 -c '
import json, os, sys, time
from aiprover_orchestration.orchestrator import prompts
from aiprover_orchestration.orchestrator.config import ALGORITHM, LEAN_ENVIRONMENT
run, problem, theorem, captain = sys.argv[1:]
config = json.load(open("configs/orchestrator/aiprover_vista_served.json"))
solver = {**config.pop("agents")["solver"], "config": os.environ["AIPROVER_CONFIG"]}
print(json.dumps({
    "run_id": run,
    "problem": json.load(open(problem)),
    "theorem_name": theorem,
    "models": {"orchestrator": captain, "worker": "aiprover"},
    "agents": {"captain": {"backend": "claude_code", "model": captain},
               "solver": solver},
    "config": config,
    "algorithm": ALGORITHM,
    "lean_environment": LEAN_ENVIRONMENT,
    "system_prompts": {"captain": prompts.CAPTAIN_SYSTEM,
                       "auditor": prompts.auditor_system(),
                       "reviewer": prompts.REVIEWER_SYSTEM,
                       "writer": prompts.WRITER_SYSTEM},
    "driver": "aiprover-captain skill",
    "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "time": int(time.time()),
}))' "$run" "$problem" "$theorem" "$captain") > "$ROOT/results/$run/trace.jsonl"
}

# Appends one step. Arguments: run id, then jq options and a filter that
# builds the step object; the current time is added as `time`.
trace_step() {
    local run=$1; shift
    jq -nc "$@" | jq -c --argjson time "$(date +%s)" '. + {time: $time}' \
        >> "$ROOT/results/$run/trace.jsonl"
}

# Runs `aiprover check` on a Lean file, records a lean_check step and prints
# the verdict; returns 0 on PASS. Arguments: run id, stage, label, file, then
# check options (--statement-only, --fixed F).
traced_check() {
    local run=$1 stage=$2 label=$3 file=$4; shift 4
    local report=${file%.lean}.check.json
    "$AIP" check "$file" --json "$@" > "$report"
    local verdict=$?
    trace_step "$run" --arg stage "$stage" --arg label "$label" \
        --arg name "$(basename "$file")" --rawfile source "$file" \
        --slurpfile check "$report" '$check[0] as $c | {
            kind: "lean_check", stage: $stage, label: $label, file: $name,
            source: $source, ok: $c.compiles, verdict: $c.verdict,
            problems: $c.problems,
            errors: (if $c.compiles then [] else [$c.diagnostics] end),
            sorry_warning: ($c.complete | not), axioms: $c.axioms}'
    jq -r '"\(.verdict)  compiles=\(.compiles) complete=\(.complete)",
        (.problems[] | "  PROBLEM: \(.)"),
        (.diagnostics | select(. != "(no errors or warnings)"))' "$report"
    return $verdict
}

# Records a finished AIProver job as a solver step. render fills in each
# sample's ending, check, Lean file and session from the job directory.
# Arguments: run id, stage, lemma name, job id, round.
traced_job() {
    local run=$1 stage=$2 lemma=$3 job=$4 round=$5
    local report=$ROOT/temp/$run/$job.result.json
    "$AIP" result "$job" --json > "$report" || return
    trace_step "$run" --arg stage "$stage" --arg lemma "$lemma" \
        --arg job "$job" --argjson round "$round" \
        --rawfile prompt "$JOBS/$job/problem.txt" \
        --slurpfile result "$report" '$result[0] as $r | {
            kind: "model_call", stage: $stage, label: ($lemma + "/aiprover"),
            role: "solver", backend: "aiprover", model: "aiprover",
            lemma: $lemma, round: $round, aiprover_job: $job,
            prompt: $prompt, response: ($r.best.status // "empty"),
            seconds: ([$r.samples[].elapsed_sec // 0] | max),
            samples: [$r.samples[] | {sample, status, turns, elapsed_sec,
                fixed_code_preserved,
                problems: (.check.problems // [])}]}'
    jq -r '"\(.job): " + ([.samples[] | "s\(.sample) \(.status)"
        + (if .fixed_code_preserved == false then " CHANGED" else "" end)]
        | join(", "))' "$report"
}

# Records a subagent call (auditor, reviewer, writer). Arguments: run id,
# stage, label, role, model, prompt file, reply file.
traced_subagent() {
    local run=$1 stage=$2 label=$3 role=$4 model=$5
    trace_step "$run" --arg stage "$stage" --arg label "$label" \
        --arg role "$role" --arg model "$model" \
        --rawfile prompt "$6" --rawfile response "$7" '{
            kind: "model_call", stage: $stage, label: $label, role: $role,
            backend: "subagent", model: $model, system_prompt: $role,
            prompt: $prompt, response: $response}'
}

# Prints the forbidden constructs (lean/checker.py) in a Lean file, one per
# line; prints nothing for a clean file.
forbidden() {
    (cd "$ROOT" && python3 -c '
import sys
from aiprover_orchestration.lean.checker import forbidden_constructs
print("\n".join(forbidden_constructs(open(sys.argv[1]).read())))' "$1")
}

# Typesets the writer's informalization as results/<run>/informal/
# <slug>_informal.pdf, with the pipeline's contract check and generated
# summary and correspondence tables. Arguments: run id, theorem name, Lean
# source file, lemmas (JSON file of [name, verified] pairs), writer model,
# the writer's reply file, and true when the Lean source is the verified file.
informal_build() {
    (cd "$ROOT" && python3 -c '
import json, sys
from pathlib import Path
from aiprover_orchestration.orchestrator.reports.informal import (
    InformalBuilder, rebuild, summary_rows)
from aiprover_orchestration.orchestrator.reports.report import ReportText
run, theorem, source, lemmas, writer, reply, verified = sys.argv[1:]
result_dir = Path("results", run)
row = json.loads((result_dir / "trace.jsonl").open().readline())["problem"]
lemmas = json.load(open(lemmas))
builder = InformalBuilder(
    result_dir / "informal", theorem, row, theorem, Path(source).read_text(),
    lemmas, summary_rows(row, run, lemmas, verified == "true", writer))
builder.save(ReportText.parse(Path(reply).read_text()))
rebuild(result_dir / "informal")' "$@")
}
