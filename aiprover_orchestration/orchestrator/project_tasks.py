"""Tasks of a project on its service, Claude or Codex, each in a process that
outlives the server.

A research task is a session that writes a report and a second that
referees it; asking for milestones is a research task whose question says so.
The referee's report supplies the project's open lemmas, its claims to review,
and its proposed milestones. A check task has the service referee one claim,
and a paper task writes the project's paper. Each task lies in
data/projects/<name>/tasks/<id>/.

Claims record a person's decision and Claude's verdict apart, since a project
may have no expert: neither is a proof, and the way to settle a claim without
one is to add it as a milestone.

    python -m aiprover_orchestration.orchestrator.project_tasks <name> <id>
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from ..agents.codex_cli import DEFAULT_CODEX_MODEL
from ..agents.codex_cli import command as codex_command
from ..utils.processes import run_group
from ..paths import LIBRARIES_DIR
from . import prompts
from .project import PAPER_MODELS, PROJECTS_DIR, paper, replace_unstarted, update
from .structures import extract_named, extract_tag

MILESTONE_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}")
ROLES = ("background", "main")

MODEL = "claude-opus-5-5"
# A session of the hand-run research took at most this long.
SESSION_SECONDS = 3 * 3600
# As in those sessions: no web, whose pages could steer a session with Python.
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
PERMISSIONS = ("Read(./**)", "Write(./**)", "Edit(./**)", "Bash(python3:*)")


def task_dir(name: str, task_id: str) -> Path:
    return PROJECTS_DIR / name / "tasks" / task_id


def claude_command(project: dict, limit: float) -> list[str]:
    """A session that reads only this project's files, which keeps projects'
    material apart."""
    readable = [PROJECTS_DIR / project["name"]]
    if project.get("source"):
        readable.append(project["source"])
    reads = [f"{rule}(/{path}/**)" for path in readable
             for rule in ("Read", "Glob", "Grep")]
    reads.append(f"Read(/{LIBRARIES_DIR / project.get('library', '')})")
    return ["claude", "-p", "--model", MODEL, "--effort", "high",
            "--tools", TOOLS, "--setting-sources", "", "--strict-mcp-config",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--max-budget-usd", str(limit), "--add-dir", *map(str, readable),
            "--allowedTools", *PERMISSIONS, *reads, "--output-format", "json"]


def session(
    project: dict, folder: Path, prompt: str, limit: float, spent: list
) -> str:
    """The report of a session in `folder`. `spent` receives its cost before
    a failure can lose it: its limit, if the session never answers. Codex
    reports no cost and has no spending limit."""
    folder.mkdir(parents=True, exist_ok=True)
    codex = project.get("service") == "codex"
    command = (
        codex_command(DEFAULT_CODEX_MODEL, "high", folder, True)
        if codex
        else claude_command(project, limit)
    )
    try:
        reply = run_group(
            command, cwd=folder, timeout=SESSION_SECONDS, stdin=prompt
        )
        payload = {} if codex else json.loads(reply.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        spent.append(limit)
        raise
    spent.append(payload.get("total_cost_usd", 0.0))
    report = folder / "report.md"
    if payload.get("is_error") or not report.exists():
        raise RuntimeError(
            f"{payload.get('subtype', 'no report')}: {reply.stderr[-500:]}"
        )
    return report.read_text()


def context(project: dict) -> str:
    """What the project has established, for a session's prompt."""
    lines = [project["title"], project.get("goal", ""), "", "Results, in order:"]
    for member in project["members"]:
        statement = member.get("statement", "")
        run = member.get("run", "not started")
        lines.append(f"- {member['uuid']} ({member['role']}, {run}) {statement}")
    lines += ["", "Open lemmas:"]
    lines += [f"- {item['lemma']}: {item['reason']}" for item in project["open"]]
    return "\n".join(lines)


def paths(project: dict) -> dict[str, str]:
    return {
        "context": context(project),
        "source": project.get("source") or "(none)",
        "library": str(LIBRARIES_DIR / project["library"]),
    }


def absorb(project: dict, report: str, replace: bool = False) -> None:
    """Add what a report found to the project, without repeating an entry."""
    known = {item["lemma"] for item in project["open"]}
    for lemma, reason in extract_named(report, "open").items():
        if lemma not in known:
            project["open"].append({"lemma": lemma, "reason": reason.strip()})
    claims = {review["claim"] for review in project["reviews"]}
    found = re.findall(r"<claim>\s*(.*?)\s*</claim>", report, flags=re.S)
    project["reviews"] += [
        {"claim": claim} for claim in dict.fromkeys(found) if claim not in claims
    ]
    # With `replace`, the report's milestones replace the unstarted ones, so
    # that a revision may change or drop them; one listed again with its
    # statement unchanged keeps the proof and dependencies the report's
    # session never saw. Otherwise only new milestones are added.
    members = {member["uuid"]: member for member in project["members"]}
    proposed = []
    for name, block in extract_named(report, "milestone").items():
        role, statement = extract_tag(block, "role"), extract_tag(block, "statement")
        member = members.get(name)
        if member and member.get("run"):
            continue
        if member and member["statement"] == statement:
            proposed.append(member)
        elif MILESTONE_NAME.fullmatch(name) and statement:
            proposed.append({
                "uuid": name,
                "role": role if role in ROLES else "main",
                "statement": statement,
                "proof": extract_tag(block, "proof"),
                "uses": re.findall(r"[\w-]+", extract_tag(block, "uses")),
            })
    if not replace:
        project["members"] += [m for m in proposed if m["uuid"] not in members]
    elif proposed:
        replace_unstarted(project, proposed)


def research(project: dict, task: dict, folder: Path, spent: list):
    filled = paths(project) | {"question": task["question"]}
    draft = session(
        project,
        folder / "draft",
        prompts.RESEARCH_TEMPLATE.format(
            **filled, tasks=folder.parent
        ),
        task["max_usd"] / 2,
        spent,
    )
    report = session(
        project,
        folder,
        prompts.RESEARCH_CRITIC_TEMPLATE.format(**filled, draft=draft),
        task["max_usd"] / 2,
        spent,
    )
    return lambda current: absorb(current, report, task.get("milestones", False))


def check(project: dict, task: dict, folder: Path, spent: list):
    report = session(
        project,
        folder,
        prompts.CLAIM_CHECK_TEMPLATE.format(
            **paths(project), claim=task["question"]
        ),
        task["max_usd"],
        spent,
    )

    def record(current: dict) -> None:
        for review in current["reviews"]:
            if review["claim"] == task["question"]:
                review["claude"] = extract_tag(report, "verdict") or "unclear"

    return record


def write_paper(project: dict, task: dict, folder: Path, spent: list):
    record = paper(project, PAPER_MODELS)
    spent.append(record["cost_usd"])
    if not record["compiled"]:
        raise RuntimeError(f"the paper did not compile: {record['problems']}")


KINDS = {"research": research, "check": check, "paper": write_paper}


def finish(name: str, task_id: str, absorbed=None, **fields) -> None:
    def change(project: dict) -> None:
        next(t for t in project["tasks"] if t["id"] == task_id).update(fields)
        if absorbed:
            absorbed(project)

    update(name, change)


def main() -> None:
    name, task_id = sys.argv[1:3]
    # Under the lock, which the server holds until the task is saved.
    project = update(name, lambda current: current)
    task = next(t for t in project["tasks"] if t["id"] == task_id)
    spent = []
    try:
        absorbed = KINDS[task["kind"]](
            project, task, task_dir(name, task_id), spent
        )
    except (Exception, SystemExit) as error:
        finish(name, task_id, state="failed", error=str(error)[:1000],
               cost_usd=sum(spent))
        raise
    finish(name, task_id, absorbed, state="done", cost_usd=sum(spent))


if __name__ == "__main__":
    main()
