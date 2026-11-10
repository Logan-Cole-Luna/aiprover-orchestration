"""Projects: groups of runs on one subject that share a library and are
written up together as one paper.

A project file (data/projects/<name>.json) lists its members in dependency
order. A member names a problem of the datasets and, once launched, its run;
its role is "background" (an established result the others build on) or
"main" (a contribution of the paper). Launching submits each member without
a run to the query server, after the member before it, on the project's
library, which each verified run extends; so every result can use the ones
before it.

    {"name": "...", "title": "...", "authors": "...", "goal": "...",
     "library": "<file in libraries/>", "gpu_group": "<optional>",
     "source": "<folder of the source paper>",
     "selection": {"writer": {"provider": "claude", "model": "..."}},
     "members": [{"uuid": "...", "role": "background", "run": "..."},
                 {"uuid": "...", "role": "main", "statement": "...",
                  "proof": "...", "uses": ["..."]}],
     "tasks": [...], "open": [...], "reviews": [...]}

A member with a `statement` is its own problem; any other names a dataset row.
`uses` names the members a member depends on, for the project's blueprint.
`tasks`, `open`, and `reviews` belong to project_tasks.py.

Without the server, the same steps run from the command line:

    python -m aiprover_orchestration.orchestrator.project run NAME \
        --config CONFIG [--dataset DATA]           # prove the members, in order
    python -m aiprover_orchestration.orchestrator.project task NAME \
        research|check|paper [QUESTION]            # one task of project_tasks.py
    python -m aiprover_orchestration.orchestrator.project review NAME \
        INDEX confirmed|rejected [NOTE]            # a person's decision on a claim

The paper is written from the members' informalizations, each already
checked against its Lean proof: the writer composes one article, the
reviewer checks it against the informalizations and the Lean statements of
the members' theorems, and the writer corrects the discrepancies once.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from ..agents import AgentPool, build_agent, role_spec
from ..paths import HOME_DIR, LOGS_DIR, RESULTS_DIR
from ..utils.files import write_atomic
from . import prompts
from .config import MAIN_NAME
from .reports.informal import INFORMAL_PREAMBLE
from .reports.report import (
    PREAMBLE,
    LatexBuilder,
    ReportText,
    escape,
    forbidden_latex,
    lean_declarations,
    unicode_declarations,
)

PROJECTS_DIR = HOME_DIR / "data" / "projects"
# Models of the paper's writer and reviewer unless the call names others.
PAPER_AGENT = {
    "backend": "claude",
    "effort": "high",
    "timeout": 2400,
    "max_output_tokens": 128000,
    "tools": False,
}
PAPER_MODELS = {"writer": "claude-sonnet-5-5", "reviewer": "claude-opus-5-5"}


def load(name: str, folder: Path | None = None) -> dict:
    path = (folder or PROJECTS_DIR) / f"{name}.json"
    return {"tasks": [], "open": [], "reviews": []} | json.loads(path.read_text())


def save(project: dict) -> None:
    path = PROJECTS_DIR / f"{project['name']}.json"
    write_atomic(path, json.dumps(project, indent=1) + "\n")


def update(name: str, change):
    """Apply `change` to a project and save it, under a lock: the server and
    the project's tasks both write. Returns the result of `change`."""
    with open(PROJECTS_DIR / f"{name}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        project = load(name)
        result = change(project)
        save(project)
        return result


def launch(project: dict, submit) -> list[str]:
    """Submit the members without a run, each after the one before it, on
    the project's library; returns the new run ids."""
    started, previous = [], ""
    for member in project["members"]:
        if member.get("run"):
            previous = member["run"]
            continue
        problem = (
            {
                "name": member["uuid"],
                "statement": member["statement"],
                "proof": member.get("proof", ""),
                # Kept as given in theorem-proving mode (stages/draft.py).
                "formal_statement": member.get("formal_statement", ""),
            }
            if member.get("statement")
            else {"uuid": member["uuid"]}
        )
        body = {
            **problem,
            "group": project.get("title", ""),
            "source_dir": project.get("source", ""),
            **({"gpu_group": project["gpu_group"]} if project.get("gpu_group") else {}),
            "library": project["library"],
            "extend_library": True,
            "after": previous,
            "settings": project.get("settings", {}),
            **project.get("selection", {}),
        }
        member["run"] = submit(body)["run_id"]
        save(project)
        started.append(member["run"])
        previous = member["run"]
    return started


def replace_unstarted(project: dict, members: list[dict]) -> None:
    """Replace the members without a run by `members`; a started result keeps
    its place and its run."""
    started = [member for member in project["members"] if member.get("run")]
    names = [member["uuid"] for member in started + members]
    if len(set(names)) < len(names):
        raise ValueError("each result needs its own name")
    project["members"] = started + members


def member_result(member: dict) -> dict:
    """A member's informalization and the Lean statement of its theorem."""
    parts = json.loads(
        (RESULTS_DIR / member["run"] / "informal" / "informal_parts.json")
        .read_text()
    )
    builder, text = parts["builder"], parts["text"]
    # The run's file states the theorem as MAIN_NAME; the library and the
    # paper use the slug name.
    theorem = next(
        source
        for kind, name, source in lean_declarations(builder["lean_source"])
        if name in (builder["theorem_name"], MAIN_NAME)
    )
    return {
        "slug": builder["slug"],
        "role": member["role"],
        "theorem_name": builder["theorem_name"],
        "statement": theorem.split(":=", 1)[0].strip(),
        "lemmas": [name for name, _ in builder["lemmas"]],
        "text": text,
        "run": member["run"],
    }


def results_block(results: list[dict]) -> str:
    return "\n\n".join(
        f'<result slug="{r["slug"]}" role="{r["role"]}">\n'
        f"<lean_statement>\n{r['statement']}\n</lean_statement>\n"
        f"<lemmas>{', '.join(r['lemmas'])}</lemmas>\n"
        f"<informalization>\n<title>{r['text']['title']}</title>\n"
        f"<macros>{r['text']['macros']}</macros>\n{r['text']['body']}\n"
        f"</informalization>\n</result>"
        for r in results
    )


class PaperBuilder(LatexBuilder):
    """Writes, checks and compiles the paper of a project."""

    phase = "paper"

    def __init__(self, project: dict, results: list[dict]):
        self.project = project
        self.results = results
        self.out_dir = PROJECTS_DIR / project["name"]
        self.name = f"{project['name']}_paper"

    def problems(self, text: ReportText) -> list[str]:
        """Contract violations of the writer's text, before compiling."""
        problems = forbidden_latex(text)
        labels = re.findall(r"\\label\{(thm|lem):([^}]*)\}", text.body)
        found = [f"{kind}:{name}" for kind, name in labels]
        for r in self.results:
            if f"thm:{r['slug']}" not in found:
                problems.append(f"no theorem labelled thm:{r['slug']}")
            if r["role"] != "main":
                continue
            missing = [
                name for name in r["lemmas"]
                if f"lem:{r['slug']}:{name}" not in found
            ]
            if missing:
                problems.append(
                    f"lemmas of {r['slug']} without "
                    f"\\label{{lem:{r['slug']}:NAME}}: {', '.join(missing)}"
                )
        repeated = sorted({label for label in found if found.count(label) > 1})
        if repeated:
            problems.append(f"labels used more than once: {', '.join(repeated)}")
        return problems

    def verification(self) -> str:
        """The machine-verified results, generated from the members."""
        rows = "\n".join(
            f"\\cref{{thm:{r['slug']}}} & \\texttt{{{escape(r['theorem_name'])}}} "
            f"& {len(r['lemmas'])} \\\\"
            for r in self.results
        )
        return (
            "\\appendix\n\\section{Machine verification}\n"
            "Every result of this paper is proved in Lean 4 with Mathlib, "
            "with the standard axioms only. Each theorem, the Lean "
            "declaration that states it, and the number of lemmas of its "
            "formal proof:\n\n"
            "\\begin{longtable}{@{}llr@{}}\n\\toprule\n"
            "Result & Lean declaration & Lemmas \\\\\n\\midrule\n\\endhead\n"
            f"{rows}\n\\bottomrule\n\\end{{longtable}}"
        )

    def document(self, text: ReportText) -> str:
        unicode, _ = unicode_declarations(
            text.body + text.abstract + text.title + text.macros
        )
        authors = self.project.get("authors", "")
        return (
            f"{PREAMBLE}{INFORMAL_PREAMBLE}{unicode}\n{text.macros}\n\n"
            f"\\title{{{text.title}}}\n\\author{{{authors}}}\n\\date{{}}\n"
            f"\\begin{{document}}\n\\maketitle\n"
            f"\\begin{{abstract}}\n{text.abstract}\n\\end{{abstract}}\n\n"
            f"{text.body}\n\n{self.verification()}\n\\end{{document}}\n"
        )

    def documents(self, text: ReportText) -> list[str]:
        return [self.document(text)]

    def write_prompt(self) -> str:
        return prompts.PAPER_TEMPLATE.format(
            title=self.project.get("title", ""),
            results=results_block(self.results),
        )

    def check_prompt(self, text: ReportText) -> str:
        return prompts.PAPER_CHECK_TEMPLATE.format(
            results=results_block(self.results), body=text.body
        )

    def save(self, text: ReportText) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{self.name}_parts.json").write_text(
            json.dumps({"title": text.title, "abstract": text.abstract,
                        "macros": text.macros, "body": text.body}, indent=1)
        )


class ProposalBuilder(PaperBuilder):
    """The project's paper while no result has a run: a proposal for the
    expert's feedback, written from the latest blueprint."""

    def __init__(self, project: dict, report: str):
        super().__init__(project, [])
        self.report = report

    def problems(self, text: ReportText) -> list[str]:
        return forbidden_latex(text)

    def verification(self) -> str:
        return ""

    def write_prompt(self) -> str:
        blueprint = "\n".join(
            f"- {m['uuid']} ({m['role']}; uses: {', '.join(m.get('uses', [])) or 'none'})"
            f" {m['statement']}"
            for m in self.project["members"]
        )
        open_items = "\n".join(
            f"- {item['lemma']}: {item['reason']}" for item in self.project["open"]
        )
        return prompts.PROPOSAL_TEMPLATE.format(
            title=self.project.get("title", ""),
            goal=self.project.get("goal", ""),
            blueprint=blueprint,
            open_items=open_items or "(none)",
            report=self.report,
        )

    def check_prompt(self, text: ReportText) -> str:
        return prompts.PROPOSAL_CHECK_TEMPLATE.format(report=self.report, body=text.body)


def blueprint_report(project: dict) -> str:
    """The latest task report that proposed a blueprint, or empty. Reports
    before the rename tag its theorems `<milestone>`."""
    for task in reversed(project["tasks"]):
        path = PROJECTS_DIR / project["name"] / "tasks" / task["id"] / "report.md"
        text = path.read_text() if path.exists() else ""
        if re.search(r"<(?:theorem|milestone) ", text):
            return text
    return ""


def paper(project: dict, models: dict[str, str]) -> dict:
    """Write the project's paper from its members' informalizations; before
    any member has a run, a proposal from the blueprint instead."""
    unfinished = [
        member["uuid"]
        for member in project["members"]
        if not member.get("run")
        or not (
            RESULTS_DIR / member["run"] / "informal" / "informal_parts.json"
        ).exists()
    ]
    started = any(member.get("run") for member in project["members"])
    report = "" if started else blueprint_report(project)
    if unfinished and not report:
        raise SystemExit(
            f"members without an informalization: {unfinished}; choose Generate "
            f"documents on each run"
        )
    results = [] if report else [member_result(m) for m in project["members"]]
    roles = {
        role: build_agent(role_spec(role, PAPER_AGENT | {"model": model}))
        for role, model in models.items()
    }
    pool = AgentPool(roles, LOGS_DIR / f"project_{project['name']}" / "calls.jsonl")
    systems = {
        "writer": prompts.WRITER_SYSTEM,
        "reviewer": prompts.REVIEWER_SYSTEM,
    }

    def complete(prompt: str, role: str, phase: str) -> str:
        return pool.complete(
            prompt, role=role, phase=phase, system_prompt=systems[role]
        )

    builder = ProposalBuilder(project, report) if report else PaperBuilder(project, results)
    record = builder.build(complete)
    cost = sum(total["cost_usd"] for total in pool.usage_by_model.values())
    return record | {"cost_usd": cost}


def run_member(body: dict, project: dict, config: Path, dataset: Path) -> dict:
    """Prove one member as launch() submits it, by a run in this process's
    place; the library is extended by the run if it is proved and reviewed."""
    name = body.get("uuid") or body["name"]
    run_id = f"{project['name']}_{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}"
    folder = PROJECTS_DIR / project["name"] / "members" / name
    folder.mkdir(parents=True, exist_ok=True)
    settings = json.loads(config.read_text())
    if body.get("source_dir") and settings["agents"]["orchestrator"].get("tools"):
        settings["agents"]["orchestrator"]["source_dir"] = body["source_dir"]
    (folder / "config.json").write_text(json.dumps(settings, indent=1))
    command = [sys.executable, "-m", "aiprover_orchestration.orchestrator.run",
               "--config", str(folder / "config.json"), "--run-id", run_id,
               "--library", f"libraries/{body['library']}", "--extend-library", "true"]  # fmt: skip
    for key, value in body.get("settings", {}).items():
        command += [f"--{key.replace('_', '-')}", str(value)]
    if "uuid" in body:
        command += ["--dataset", str(dataset), "--problem-uuid", body["uuid"]]
    for field in ("statement", "proof", "formal_statement"):
        if body.get(field):
            (folder / f"{field}.txt").write_text(body[field])
            command += [f"--{field.replace('_', '-')}", str(folder / f"{field}.txt")]
    subprocess.run(command, env=os.environ | library_environment(body["library"]))
    return {"run_id": run_id}


def library_environment(library: str) -> dict:
    """The variable that makes a package library the runs' workspace."""
    from . import libraries

    path = f"libraries/{library}"
    if library and libraries.is_package(path):
        return {"AIPROVER_LIBRARY_PACKAGE": str(libraries.resolve(path))}
    return {}


def start_task(name: str, kind: str, question: str) -> None:
    """Run one task of project_tasks.py in the foreground, as the page starts it."""
    from .project_tasks import task_dir

    task = {"kind": kind, "question": question, "max_usd": 20, "blueprint": False,
            "id": time.strftime("%Y%m%d_%H%M%S"), "state": "running",
            "started": time.time()}  # fmt: skip

    def append(project: dict) -> dict:
        if any(other.get("state") == "running" for other in project["tasks"]):
            raise SystemExit("a task of this project is running")
        project["tasks"].append(task)
        return project

    project = update(name, append)
    folder = task_dir(name, task["id"])
    folder.mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "aiprover_orchestration.orchestrator.project_tasks",
         name, task["id"]],
        cwd=folder, env=os.environ | library_environment(project.get("library", "")),
    )  # fmt: skip


def decide(name: str, index: int, decision: str, note: str) -> None:
    def change(project: dict) -> None:
        project["reviews"][index] |= {"decision": decision, "note": note,
                                      "by": os.environ.get("USER", "")}  # fmt: skip

    update(name, change)


def main() -> None:
    parser = argparse.ArgumentParser(description="Projects without the server")
    actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", help="prove the members without a run, in order")
    run.add_argument("name")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--dataset", type=Path, default=HOME_DIR / "data" / "openai_math.jsonl")
    task = actions.add_parser("task", help="research, check a claim, or write the paper")
    task.add_argument("name")
    task.add_argument("kind", choices=("research", "check", "paper"))
    task.add_argument("question", nargs="?", default="")
    review = actions.add_parser("review", help="a person's decision on a claim")
    review.add_argument("name")
    review.add_argument("index", type=int)
    review.add_argument("decision", choices=("confirmed", "rejected"))
    review.add_argument("note", nargs="?", default="")
    args = parser.parse_args()
    if args.action == "run":
        project = load(args.name)
        launch(project, lambda body: run_member(body, project, args.config, args.dataset))
    elif args.action == "task":
        start_task(args.name, args.kind, args.question)
    else:
        decide(args.name, args.index, args.decision, args.note)


if __name__ == "__main__":
    main()
