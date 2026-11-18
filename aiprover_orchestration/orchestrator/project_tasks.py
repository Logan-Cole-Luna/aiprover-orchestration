"""Tasks of a project on its service, Claude or Codex, each in a process that
outlives the server.

A research task is a session that writes a report and a second that
referees it; asking for a blueprint is a research task whose question says so.
An explore task has several sessions work on one question at once, each
choosing its direction from what the others post to a shared ledger, and a
referee that reads a digest of the ledger and their reports. The referee's
report supplies the project's open lemmas, its claims to review, and its
proposed blueprint. A paper task writes the project's paper. Each task lies in
data/projects/<name>/tasks/<id>/.

A claim is not a proof; the way to settle one is to add it as a theorem to
prove. The check task, which had the service referee one claim, is archived.

    python -m aiprover_orchestration.orchestrator.project_tasks <name> <id>
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..agents.base import TOKENS_PER_USD, TURNS_PER_USD, billed_tokens
from ..agents.codex_cli import DEFAULT_CODEX_MODEL, parse_events, token_usage
from ..agents.codex_cli import command as codex_command
from ..agents.common import (
    FINDINGS_BIN,
    FINDINGS_NOTE,
    LEAN_CHECK_BIN,
    MAX_RETRIES,
    PAPER_SEARCH_BIN,
    PAPER_SEARCH_NOTE,
    SAT_SOLVE_BIN,
    SAT_SOLVE_NOTE,
    SINGULAR_BIN,
    SINGULAR_NOTE,
    failed_call,
    make_lean_project,
)
from ..agents.session_tools import TURN_LIMIT
from ..cas import singular
from ..findings.findings import digest, entries
from ..utils.processes import run_group
from ..paths import LIBRARIES_DIR
from . import prompts
from .project import PAPER_MODELS, PROJECTS_DIR, paper, replace_unstarted, update
from .structures import extract_named, extract_tag

THEOREM_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}")
ROLES = ("background", "main")

MODEL = "claude-opus-5-5"
# A session of the hand-run research took at most this long.
SESSION_SECONDS = 3 * 3600
# As in those sessions: no web, whose pages could steer a session with Python.
# `python3` resolves to bin/python3, which confines it to the session's files.
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
PERMISSIONS = (
    "Read(./**)", "Write(./**)", "Edit(./**)", "Bash(python3:*)",
    "Bash(lean-check:*)",
)  # fmt: skip
SESSION_BIN = Path(__file__).resolve().parent / "bin"
# A final message this long is taken for the report itself.
REPORT_CHARS = 2000
# The agents of an explore task unless it names their number.
AGENTS = 3
# The referee of an explore task reads at most this much of its agents'
# reports inline, and the rest from their files; and a digest of their ledger
# of this length.
MAX_DRAFT_CHARS = 300_000
# The part of an agent's limit kept for reconciling its report at the end.
RECONCILE_SHARE = 0.2
DIGEST_CHARS = 20_000
# Every session writes its report as it goes, so that a session that ends
# early, at a limit or by an error, leaves what it found.
REPORT_NOTE = """

Write `report.md` early, with an outline and your findings so far, and update
it as you work. A session can end at any turn, and only the file is kept: your
final message is not the report."""
# A Codex session reaches files only through agents/session_tools.py; its own
# patch tool is refused by the read-only sandbox, and it has no sub-agents.
CODEX_NOTE = """

Tools. Read the project's files with `read_file`, `list_files`, and `grep`.
Write files, including `report.md`, with `write_file` and `edit_file`; paths
are relative to the current directory. Run a Python script you have written
with `python3`, whose argument is the script's path; it has no network. Check a
Lean file with `lean_check`, whose argument is its path. There
is no shell, no patch tool, no web search, and no sub-agent: do all the work
yourself with these tools."""
SANDBOX = Path(__file__).resolve().parents[1] / "lean" / "sandbox.py"


def task_dir(name: str, task_id: str) -> Path:
    return PROJECTS_DIR / name / "tasks" / task_id


def readable_files(project: dict) -> list[Path]:
    """What a session reads, which keeps projects' material apart."""
    readable = [PROJECTS_DIR / project["name"]] if "name" in project else []
    if project.get("source"):
        readable.append(Path(project["source"]))
    return readable


def optional_tools(project: dict) -> list[tuple[str, Path, str, str]]:
    """The optional commands of a session, each with its directory, its note,
    and the variable of its log: `paper-search` and `sat-solve` when the
    project asks for them, `singular` on a site that names Singular, and
    `findings` for the agents of an explore task, whose ledger is its log."""
    tools = [
        ("paper-search", PAPER_SEARCH_BIN, PAPER_SEARCH_NOTE, "PAPER_SEARCH_LOG"),
        ("sat-solve", SAT_SOLVE_BIN, SAT_SOLVE_NOTE, "SAT_SEARCH_LOG"),
        ("singular", SINGULAR_BIN, SINGULAR_NOTE, "SINGULAR_LOG"),
        ("findings", FINDINGS_BIN, FINDINGS_NOTE, "FINDINGS_DIR"),
    ]
    enabled = (project.get("paper_search"), project.get("sat_search"),
               singular.executable(), project.get("findings"))  # fmt: skip
    return [tool for tool, wanted in zip(tools, enabled) if wanted]


def session_environment(project: dict, folder: Path) -> dict[str, str]:
    """The search path and the trees of the session Python (bin/python3)."""
    trees = readable_files(project)
    if project.get("library"):
        trees.append(LIBRARIES_DIR / project["library"])
    tools = optional_tools(project)
    # Each command logs its calls in the session's folder; searches go to the
    # run's log instead when a run set one.
    logs = {}
    for name, _, _, variable in tools:
        own = str(folder / f"{name.replace('-', '_')}.jsonl")
        logs[variable] = own if name == "paper-search" else os.environ.get(variable, own)
    if project.get("findings"):
        # An explore task's agents post to its shared folder under their
        # folders' names.
        logs |= {"FINDINGS_DIR": project["findings"], "FINDINGS_AUTHOR": folder.name}
    if project.get("findings_read_only"):
        logs["FINDINGS_READ_ONLY"] = "1"
    bins = [SESSION_BIN, LEAN_CHECK_BIN, *(path for _, path, _, _ in tools)]
    return {
        **os.environ,
        **logs,
        "PATH": ":".join([*map(str, bins), os.environ.get("PATH", "")]),
        "SESSION_PYTHON": sys.executable,
        # A virtualenv's standard library lies under its base prefix.
        "SESSION_PREFIX": ":".join(dict.fromkeys((sys.prefix, sys.base_prefix))),
        "SESSION_SANDBOX": str(SANDBOX),
        "SESSION_READ": ":".join(map(str, trees)),
        "SESSION_WRITE": str(folder),
    }


def claude_command(project: dict, limit: float) -> list[str]:
    """A session that reads only this project's files."""
    readable = readable_files(project)
    reads = [f"{rule}(/{path}/**)" for path in readable
             for rule in ("Read", "Glob", "Grep")]
    reads.append(f"Read(/{LIBRARIES_DIR / project.get('library', '')})")
    reads += [f"Bash({name}:*)" for name, *_ in optional_tools(project)]
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
    reports tokens, so its limit is on turns and its cost is converted."""
    folder.mkdir(parents=True, exist_ok=True)
    make_lean_project(folder)
    codex = project.get("service") == "codex"
    command = (
        codex_command(
            DEFAULT_CODEX_MODEL,
            "high",
            folder,
            True,
            read=tuple(map(str, readable_files(project)))
            + ((str(LIBRARIES_DIR / project["library"]),) if project.get("library") else ()),
            tools=("python3", "lean-check")
            + ("paper-search",) * bool(project.get("paper_search"))
            + ("findings",) * bool(project.get("findings")),
            max_turns=round(limit * TURNS_PER_USD),
        )
        if codex
        else claude_command(project, limit)
    )
    # A session refused on the account's usage limit starts again once the
    # limit resets, as a run's calls do (agents/pool.py). Every attempt's
    # cost counts.
    cost = 0.0
    for attempt in range(MAX_RETRIES + 1):
        try:
            reply = run_group(
                command,
                cwd=folder,
                timeout=SESSION_SECONDS,
                stdin=prompt
                + REPORT_NOTE
                + (CODEX_NOTE if codex else "")
                + "".join(note for _, _, note, _ in optional_tools(project)),
                env=session_environment(project, folder),
            )
            payload = {} if codex else json.loads(reply.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            spent.append(cost + limit)
            # A session stopped at its time limit leaves the report it wrote.
            if (folder / "report.md").exists():
                return (folder / "report.md").read_text()
            raise
        if codex:
            _, tokens, error = parse_events(reply.stdout)
            cost += billed_tokens(token_usage(tokens)) / TOKENS_PER_USD
        else:
            error = str(payload.get("result") or "") if payload.get("is_error") else None
            cost += payload.get("total_cost_usd", 0.0)
        wait = failed_call(error, {}).retry_after_s if error else None
        if wait is None or attempt == MAX_RETRIES:
            break
        time.sleep(wait)
    spent.append(cost)
    report = folder / "report.md"
    # The session's last message, kept for diagnosis; one long enough to be a
    # report stands in for a report the session did not write to its file.
    final = parse_events(reply.stdout)[0] if codex else payload.get("result", "")
    (folder / "reply.md").write_text(final or "")
    if codex:
        (folder / "events.jsonl").write_text(reply.stdout)
    if not report.exists() and len(final or "") >= REPORT_CHARS:
        report.write_text(final)
    if not report.exists():
        raise RuntimeError(
            no_report(payload.get("subtype", ""), reply, limit, spent[-1])
            + activity(reply.stdout, payload, final)
        )
    return report.read_text()


def activity(output: str, payload: dict, final: str) -> str:
    """What a session did before it ended: its tool calls and the start of
    its last message."""
    calls = []
    for line in output.splitlines():
        try:
            item = json.loads(line).get("item") or {}
        except (json.JSONDecodeError, AttributeError):
            continue
        if item.get("type") in ("mcp_tool_call", "command_execution"):
            calls.append(item.get("tool") or item.get("command") or "?")
    count = len(calls) if calls else payload.get("num_turns")
    text = f" It made {count} tool calls" if count else " It made no tool calls"
    if calls:
        text += f", the last {', '.join(calls[-3:])}"
    text += "."
    if final.strip():
        text += f" Its last message begins: {final.strip()[:200]!r}"
    return text


def no_report(subtype: str, reply, limit: float, cost: float) -> str:
    """Why a session ended without its report, in terms of what to change."""
    again = "Run it again with a higher limit."
    turns = round(limit * TURNS_PER_USD)
    if subtype == "error_max_turns" or TURN_LIMIT in reply.stdout:
        return (
            f"Stopped at its turn limit before writing its report: {turns} "
            f"tool calls, which its limit of ${limit:g} allows "
            f"({TURNS_PER_USD} per USD). {again}"
        )
    if subtype == "error_max_budget_usd" or cost >= limit:
        return (
            f"Stopped at its spending limit of ${limit:g} (spent ${cost:.2f}) "
            f"before writing its report. {again}"
        )
    errors = [line for line in reply.stderr.splitlines() if "ERROR" in line]
    failure = parse_events(reply.stdout)[2]
    if failure:
        errors.append(failure)
    detail = f" Last errors: {' '.join(errors[-2:])[-400:]}" if errors else ""
    return (
        "Ended without writing its report (report.md); its last message is in"
        f" reply.md of the task folder.{detail}"
    )


def context(project: dict) -> str:
    """What the project has established, for a session's prompt."""
    lines = [project["title"], project.get("goal", ""), "", "Results, in order:"]
    for member in project["members"]:
        statement = member.get("statement", "")
        run = member.get("run", "not started")
        lines.append(f"- {member['uuid']} ({member['role']}, {run}) {statement}")
    lines += ["", "Open lemmas:"]
    lines += [
        f"- {item['lemma']}: {item['reason']}"
        for item in project["open"]
        if not item.get("retired")
    ]
    return "\n".join(lines)


def paths(project: dict) -> dict[str, str]:
    return {
        "context": context(project),
        "source": project.get("source") or "(none)",
        "library": str(LIBRARIES_DIR / project["library"]),
    }


# Why a referee retires a claim or an open statement.
RETIRE_REASONS = ("refuted", "superseded", "proved")


def number_claims(project: dict) -> None:
    """Give each claim without one an id, C1, C2, ..., in order."""
    taken = [int(r["id"][1:]) for r in project["reviews"] if r.get("id", "")[1:].isdigit()]
    count = max(taken, default=0)
    for review in project["reviews"]:
        if not review.get("id"):
            count += 1
            review["id"] = f"C{count}"


def ledger_items(project: dict) -> dict[str, dict]:
    """The claims and open statements by id (a claim's id, an open
    statement's name)."""
    number_claims(project)
    return {r["id"]: r for r in project["reviews"]} | {o["lemma"]: o for o in project["open"]}


def ledger(project: dict) -> str:
    """The claims and open statements a referee maintains: those not
    retired, by id."""
    items = ledger_items(project)
    lines = [
        f"- {key}: {item.get('claim') or item.get('reason', '')}"
        for key, item in items.items()
        if not item.get("retired")
    ]
    return "\n".join(lines) or "(none)"


def absorb(
    project: dict, report: str, replace: bool = False, task_id: str = ""
) -> None:
    """Add what a report found to the project. Each open statement and claim
    the report names, restates (`<same id>`) or leaves open again records
    `task_id`, the last task that named it, and is active again; one it
    retires (`<retire id reason>`) keeps the reason. Repeats are not added."""
    tag = {"task": task_id} if task_id else {}
    items = ledger_items(project)

    def named(item: dict) -> None:
        item.update(tag)
        item.pop("retired", None)

    for lemma, reason in extract_named(report, "open").items():
        if lemma in items and "lemma" in items[lemma]:
            named(items[lemma])
        else:
            project["open"].append({"lemma": lemma, "reason": reason.strip()} | tag)
    claims = {review["claim"]: review for review in project["reviews"]}
    found = re.findall(r"<claim>\s*(.*?)\s*</claim>", report, flags=re.S)
    for claim in dict.fromkeys(found):
        if claim in claims:
            named(claims[claim])
        else:
            project["reviews"].append({"claim": claim} | tag)
    number_claims(project)
    for key in re.findall(r'<same\s+id="([^"]+)"\s*/?>', report):
        if key in items:
            named(items[key])
    retired = re.findall(
        rf'<retire\s+id="([^"]+)"\s+reason="({"|".join(RETIRE_REASONS)})"\s*>(.*?)</retire>',
        report,
        flags=re.S,
    )
    for key, reason, note in retired:
        if key in items:
            items[key]["retired"] = {"reason": reason, "note": note.strip(), "by": task_id}
    # With `replace`, the report's theorems replace the unstarted ones, so
    # that a revision may change or drop them; one listed again with its
    # statement unchanged keeps the proof and dependencies the report's
    # session never saw. Otherwise only new theorems are added. Reports
    # before the rename tag them `<milestone>`.
    members = {member["uuid"]: member for member in project["members"]}
    proposed = []
    tagged = {**extract_named(report, "milestone"), **extract_named(report, "theorem")}
    for name, block in tagged.items():
        role, statement = extract_tag(block, "role"), extract_tag(block, "statement")
        member = members.get(name)
        if member and member.get("run"):
            continue
        if member and member["statement"] == statement:
            proposed.append(member)
        elif THEOREM_NAME.fullmatch(name) and statement:
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


def continuation(project: dict, task: dict) -> str:
    """Where the research task that `task` continues left off, if it names
    one: its outcome, and its report to read first."""
    earlier = next(
        (other for other in project["tasks"] if other["id"] == task.get("after")),
        None,
    )
    if earlier is None:
        return ""
    # A task whose process died is still recorded as running.
    outcome = "finished" if earlier["state"] == "done" else "failed"
    if earlier.get("error"):
        outcome += f" ({earlier['error']})"
    report = task_dir(project["name"], earlier["id"]) / "report.md"
    where = (
        f" Its report is `{report}`; read it before anything else."
        if report.exists()
        else " It wrote no report, so cite nothing from it."
    )
    return f"This task continues research task {earlier['id']}, which {outcome}.{where}\n\n"


def outcome(report: str) -> str:
    """`argument` or `open`, from the first line of a report that states it."""
    first = report.strip().splitlines()[:1]
    found = re.match(r"\W*outcome\W*(argument|open)", first[0], re.I) if first else None
    return found[1].lower() if found else ""


def referee(
    project: dict, task: dict, folder: Path, spent: list, limit: float,
    draft: str, files: str, **fields,
):  # fmt: skip
    """The referee of a draft whose files lie in `files` of `folder`; what
    survives joins the project, and `fields` join the task."""
    report = session(
        project,
        folder,
        prompts.RESEARCH_CRITIC_TEMPLATE.format(
            **paths(project), question=continuation(project, task) + task["question"],
            draft=draft, files=files, tasks=folder.parent, ledger=ledger(project),
        ),  # fmt: skip
        limit,
        spent,
    )
    replace = task.get("blueprint", task.get("milestones", False))

    def absorbed(current: dict) -> None:
        absorb(current, report, replace, task["id"])
        record = next(t for t in current["tasks"] if t["id"] == task["id"])
        record.update(outcome=outcome(report), **fields)

    return absorbed


def research(project: dict, task: dict, folder: Path, spent: list):
    """A research task: the investigation's method with every tool, then a
    referee; what survives joins the project."""
    question = continuation(project, task) + task["question"]
    filled = paths(project) | {"question": question}
    project = {**project, "paper_search": True, "sat_search": True}
    draft = session(
        project,
        folder / "draft",
        prompts.RESEARCH_TEMPLATE.format(
            **filled, tasks=folder.parent
        ),
        task["max_usd"] / 2,
        spent,
    )
    return referee(project, task, folder, spent, task["max_usd"] / 2, draft, "`draft/`")


def explore(project: dict, task: dict, folder: Path, spent: list):
    """An explore task: agents at once on one question, with the tools of
    research. No one assigns their directions: each posts its sketch to the
    shared ledger first and then its findings, and chooses by what the others
    posted. A failed agent leaves its report, if any, and its entries. Once
    all agents end, each reconciles its report with the entries posted after
    its last read, in a short second session (RECONCILE_SHARE of its limit),
    since an agent that ends early never sees the others' last findings. A
    referee then reads the ledger's digest and every report, and the entries
    it needs through `findings`, which it may not post to. The task's limit is
    split evenly between the agents and the referee, and the task records the
    agents that ended with an error and those whose first entry was not a
    sketch."""
    count = task.get("agents", AGENTS)
    shared = folder / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    names = [f"agent_{k}" for k in range(1, count + 1)]
    limit = task["max_usd"] / (count + 1)
    project = {**project, "paper_search": True, "sat_search": True}
    filled = paths(project) | {"question": task["question"], "agents": count,
                               "shared": shared, "tasks": folder.parent}  # fmt: skip
    reports, failed = {}, {}

    def agent(name: str, template: str, share: float) -> None:
        # The session appends its cost to `spent` itself; list.append is atomic.
        try:
            reports[name] = session(
                {**project, "findings": str(shared)},
                folder / name,
                template.format(**filled, agent=name),
                limit * share,
                spent,
            )
        except Exception as error:
            failed.setdefault(name, str(error)[:500])
            partial = folder / name / "report.md"
            if partial.exists():
                reports[name] = partial.read_text()

    rounds = [(prompts.EXPLORE_TEMPLATE, 1 - RECONCILE_SHARE),
              (prompts.EXPLORE_RECONCILE_TEMPLATE, RECONCILE_SHARE)]  # fmt: skip
    for template, share in rounds:
        threads = [threading.Thread(target=agent, args=(name, template, share)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    ledger = entries(shared)
    first = {}
    for entry in ledger:
        first.setdefault(entry["agent"], entry["kind"])
    unsketched = [name for name in names if first.get(name) != "sketch"]
    draft = explore_draft(names, reports, failed, digest(ledger, time.time(), DIGEST_CHARS))
    files = ", ".join(f"`{name}/`" for name in names) + ", and the shared folder `shared/`"
    reader = {**project, "findings": str(shared), "findings_read_only": True}
    return referee(reader, task, folder, spent, limit, draft, files,
                   agent_errors=failed, missing_sketch=unsketched)  # fmt: skip


def explore_draft(names: list[str], reports: dict, failed: dict, summary: str) -> str:
    """What the agents of an explore task left, for its referee: the digest of
    their ledger and each report, cut so that the whole stays within
    MAX_DRAFT_CHARS."""
    share = max(2000, MAX_DRAFT_CHARS // len(names))
    parts = [
        f"This draft is the work of {len(names)} agents on the question, each "
        "in its own folder, who shared their sketches and results in a ledger. "
        "Below are the ledger's digest and each agent's report. Read the "
        "entries you need with `findings read <id>`, `findings list --topic "
        "<prefix>`, or `findings since 0 --topic <prefix>`; you cannot post. "
        "Their cited files lie in `shared/files/`.",
        f"## Digest of the ledger\n\n{summary}",
    ]
    for name in names:
        ended = f" (it ended early: {failed[name]})" if name in failed else ""
        text = reports.get(name, "(no report)").strip()
        if len(text) > share:
            text = text[:share] + f"\n\n(Cut here; the whole report is `{name}/report.md`.)"
        parts.append(f"## Report of {name}{ended}\n\n{text}")
    return "\n\n".join(parts)


# def check(project: dict, task: dict, folder: Path, spent: list):
#     report = session(
#         project,
#         folder,
#         prompts.CLAIM_CHECK_TEMPLATE.format(
#             **paths(project), claim=task["question"]
#         ),
#         task["max_usd"],
#         spent,
#     )
#
#     def record(current: dict) -> None:
#         for review in current["reviews"]:
#             if review["claim"] == task["question"]:
#                 review["claude"] = extract_tag(report, "verdict") or "unclear"
#
#     return record


def write_paper(project: dict, task: dict, folder: Path, spent: list):
    record = paper(project, PAPER_MODELS)
    spent.append(record["cost_usd"])
    if not record["compiled"]:
        raise RuntimeError(f"the paper did not compile: {record['problems']}")


KINDS = {"research": research, "explore": explore, "paper": write_paper}


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
