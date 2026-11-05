"""One confined, measured Claude session on one benchmark problem.

    python3 benchmark/run.py PROBLEM [--condition no_lean] [--repeat 0]
        [--model ID] [--prompt-file FILE] [--continue-from RUN_DIR]

The record lies in benchmark/runs/<problem>/<condition>/r<repeat>/: the
prompt, a manifest, the transcript, the result, and the session's files in
work/. A record is never overwritten.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiprover_orchestration.agents.common import (  # noqa: E402
    LEAN_PROJECT_FILES,
)
from aiprover_orchestration.paths import WORKSPACE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "benchmark"
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
PACKAGES = (WORKSPACE / ".lake" / "packages").resolve()
# The shell commands of a session with Lean; each part of a pipeline needs a
# rule. The filters read a compiler's output.
LEAN_COMMANDS = (
    "Bash(lake env lean:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(grep:*)",
)  # fmt: skip


CONTINUATION = """

## Earlier work

An earlier session on this problem was interrupted before it finished. Its
Lean files are in the working directory. Read them first and continue from
them.
"""


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def problem_prompt(problem: str, condition: dict) -> str:
    entry = read_json(HERE / "problems.json")[problem]
    with open(ROOT / entry["dataset"]) as dataset:
        rows = map(json.loads, dataset)
        row = next(r for r in rows if r["uuid"] == entry["uuid"])
    template = (HERE / "prompts" / condition["prompt"]).read_text()
    return template.format(
        informal_statement=row["informal_statement"],
        informal_proof=row["informal_proof"],
    )


def claude_command(condition: dict, binary: str) -> list[str]:
    tools = condition["tools"]
    allowed = [f"{tool}(./**)" for tool in tools if tool != "Bash"]
    if condition.get("lean"):
        allowed += [
            *LEAN_COMMANDS,
            *[f"{tool}(/{PACKAGES}/**)" for tool in ("Read", "Glob", "Grep")],
        ]
    return [
        binary, "-p",
        "--model", condition["model"],
        "--effort", condition["effort"],
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--tools", ",".join(tools),
        "--permission-mode", "dontAsk",
        "--allowedTools", *allowed,
        "--max-budget-usd", str(condition["max_budget_usd"]),
    ]  # fmt: skip


def prepare(run: Path, lean: bool) -> None:
    """The session's directories, its one link to the caller's home, and,
    with Lean, a Lake project on the workspace's packages."""
    for name in ("work", "tmp", "home/.claude"):
        (run / name).mkdir(parents=True)
    (run / "home/.claude/.credentials.json").symlink_to(CREDENTIALS)
    if lean:
        for name in LEAN_PROJECT_FILES:
            shutil.copy(WORKSPACE / name, run / "work" / name)
        (run / "work/.lake").mkdir()
        (run / "work/.lake/packages").symlink_to(PACKAGES)


def session(run: Path, prompt: str, condition: dict, binary: str) -> int:
    """Run the CLI under confine.py; its exit code."""
    command = [
        sys.executable, "-I", str(HERE / "confine.py"),
        "--run", str(run), "--credentials", str(CREDENTIALS),
        "--execute", binary,
        *(("--lean", "yes", "--read", str(PACKAGES))
          if condition.get("lean") else ()),
        "--", *claude_command(condition, binary),
    ]  # fmt: skip
    with open(run / "transcript.jsonl", "w") as out, open(
        run / "stderr.log", "w"
    ) as err:
        process = subprocess.Popen(
            command,
            cwd=run / "work",
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            text=True,
            start_new_session=True,
        )
        process.stdin.write(prompt)
        process.stdin.close()
        return process.wait()


def measured(transcript: Path, work: Path) -> dict:
    """Cost, tokens, turns, and tool audit of a session's transcript."""
    final, tools, denied = None, {}, 0
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            final = event
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else ()
        for block in content if isinstance(content, list) else ():
            if block.get("type") == "tool_use":
                tools[block["name"]] = tools.get(block["name"], 0) + 1
            if block.get("type") == "tool_result" and block.get("is_error"):
                denied += 1
    final = final or {}
    return {
        "subtype": final.get("subtype"),
        "cost_usd": final.get("total_cost_usd"),
        "turns": final.get("num_turns"),
        "model_usage": final.get("modelUsage"),
        "tool_calls": tools,
        "tool_errors": denied,
        "solution": (work / "Solution.lean").exists(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("problem")
    parser.add_argument("--condition", default="no_lean")
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--model", default="")
    parser.add_argument("--prompt-file", default="")
    parser.add_argument("--continue-from", default="")
    options = parser.parse_args()
    condition = read_json(HERE / "conditions.json")[options.condition]
    if options.model:
        condition["model"] = options.model
    prompt = (
        Path(options.prompt_file).read_text()
        if options.prompt_file
        else problem_prompt(options.problem, condition)
    )
    run = (
        HERE / "runs" / options.problem / options.condition
        / f"r{options.repeat}"
    ).resolve()  # fmt: skip
    if run.exists():
        sys.exit(f"{run} exists; a record is never overwritten")
    prepare(run, condition.get("lean", False))
    if options.continue_from:
        previous = Path(options.continue_from).resolve()
        for file in sorted((previous / "work").glob("*.lean")):
            shutil.copy(file, run / "work" / file.name)
        prompt += CONTINUATION
        # The continuation inherits only what the earlier run left unspent.
        spent = read_json(previous / "result.json")
        caps = read_json(previous / "manifest.json")["condition"]
        condition["max_budget_usd"] = round(
            caps["max_budget_usd"] - spent["cost_usd"], 2
        )
    binary = os.path.realpath(shutil.which("claude"))
    (run / "prompt.md").write_text(prompt)
    manifest = {
        "problem": options.problem,
        "condition": condition,
        "repeat": options.repeat,
        "continues": options.continue_from,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "claude_cli": Path(binary).name,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (run / "manifest.json").write_text(json.dumps(manifest, indent=1))
    start = time.time()
    code = session(run, prompt, condition, binary)
    (run / "home/.claude/.credentials.json").unlink(missing_ok=True)
    result = {
        "exit_code": code,
        "wall_seconds": round(time.time() - start, 1),
        **measured(run / "transcript.jsonl", run / "work"),
    }
    (run / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in (
        "exit_code", "wall_seconds", "cost_usd", "turns", "solution")}))  # fmt: skip


if __name__ == "__main__":
    main()
