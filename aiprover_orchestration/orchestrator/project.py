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
     "library": "<file in libraries/>", "gpu_group": "dgx",
     "source": "<folder of the source paper>",
     "selection": {"writer": {"provider": "claude", "model": "..."}},
     "members": [{"uuid": "...", "role": "background", "run": "..."},
                 {"uuid": "...", "role": "main", "statement": "...",
                  "proof": "...", "uses": ["..."]}],
     "tasks": [...], "open": [...], "reviews": [...]}

A member with a `statement` is its own problem; any other names a dataset row.
`uses` names the members a member depends on, for the project's blueprint.
`tasks`, `open`, and `reviews` belong to project_tasks.py.

The paper is written from the members' informalizations, each already
checked against its Lean proof: the writer composes one article, the
reviewer checks it against the informalizations and the Lean statements of
the members' theorems, and the writer corrects the discrepancies once.
"""

import fcntl
import json
import re
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


def load(name: str, folder: Path = PROJECTS_DIR) -> dict:
    path = folder / f"{name}.json"
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
            }
            if member.get("statement")
            else {"uuid": member["uuid"]}
        )
        body = {
            **problem,
            "group": project.get("title", ""),
            "source_dir": project.get("source", ""),
            "gpu_group": project.get("gpu_group", "dgx"),
            "library": project["library"],
            "extend_library": True,
            "after": previous,
            **project.get("selection", {}),
        }
        member["run"] = submit(body)["run_id"]
        save(project)
        started.append(member["run"])
        previous = member["run"]
    return started


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


def paper(project: dict, models: dict[str, str]) -> dict:
    """Write the project's paper from its members' informalizations."""
    unfinished = [
        member["uuid"]
        for member in project["members"]
        if not member.get("run")
        or not (
            RESULTS_DIR / member["run"] / "informal" / "informal_parts.json"
        ).exists()
    ]
    if unfinished:
        raise SystemExit(
            f"members without an informalization: {unfinished}; choose Generate "
            f"documents on each run"
        )
    results = [member_result(member) for member in project["members"]]
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

    record = PaperBuilder(project, results).build(complete)
    cost = sum(total["cost_usd"] for total in pool.usage_by_model.values())
    return record | {"cost_usd": cost}
