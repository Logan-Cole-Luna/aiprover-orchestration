"""The blueprint of a proof plan: every lemma in natural language and in Lean,
with its dependencies and status. `blueprint.json` feeds the server's page, and
`content.tex` holds the same plan in the format of leanblueprint,
https://github.com/PatrickMassot/leanblueprint.

The size measures of a run (`run_metrics`) serve the page's Details tab.
"""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from ...utils.files import read_json, read_record, write_atomic
from ..config import MAIN_NAME
from ..structures import (
    Formalization,
    Sketch,
    extract_named,
    extract_tag,
    refers_to,
    statement_key,
)
from .report import escape

CHARACTERS_PER_TOKEN = 3.5
# Lemmas of one run are proved concurrently.
PLAN_LOCK = threading.Lock()
# Who proved a lemma, by the `worker` of its step; any other is a prover.
PROVERS = {"orchestrator": "Orchestrator", "manual": "Manual"}


def parse_reply(reply: str, statements: dict[str, str]) -> dict[str, dict]:
    """The natural-language text of the reply by `statement_key`; `statements`
    maps each name to its key, so that a name the plan lacks is left out."""
    entries = {}
    for name, block in extract_named(reply, "lemma").items():
        if name in statements:
            uses = [
                word.strip() for word in extract_tag(block, "uses").split(",")
            ]
            entries[statements[name]] = {
                "statement": extract_tag(block, "statement"),
                "idea": extract_tag(block, "idea"),
                "uses": [word for word in uses if word],
            }
    return entries


def plan_nodes(
    form: Formalization, sketch: Sketch, texts: dict, verified: bool = False
) -> list[dict]:
    """The lemmas and the theorem, each with the names it uses: those the
    writer lists and those its Lean text mentions. The theorem is proved only
    by a `verified` assembly, not by its lemmas alone."""
    names = [lemma.name for lemma in sketch.lemmas]
    lemmas = [
        (
            lemma.name,
            lemma.statement,
            f"{lemma.statement}\n{lemma.helpers}\n{lemma.proof}",
            "proved"
            if lemma.proved
            else "refuted" if lemma.refutation else "planned",
        )
        for lemma in sketch.lemmas
    ]
    theorem = (
        MAIN_NAME,
        form.statement,
        sketch.main_proof,
        "proved" if verified else "planned",
    )
    nodes = []
    for name, lean, code, status in [*lemmas, theorem]:
        text = texts.get(statement_key(lean), {})
        listed = set(text.get("uses", ()))
        nodes.append(
            {
                "name": name,
                "lean": lean,
                "statement": text.get("statement", ""),
                "idea": text.get("idea", ""),
                "uses": [
                    other
                    for other in names
                    if other != name
                    and (other in listed or refers_to(code, other))
                ],
                "status": status,
            }
        )
    return nodes


def latex(nodes: list[dict]) -> str:
    """The plan as the body of a leanblueprint `content.tex`."""
    blocks = []
    for node in nodes:
        kind = "theorem" if node["name"] == MAIN_NAME else "lemma"
        uses = ", ".join(node["uses"])
        uses = f"\n  \\uses{{{uses}}}" if uses else ""
        done = "\\leanok" if node["status"] == "proved" else ""
        blocks.append(
            f"\\begin{{{kind}}}\\label{{{node['name']}}}\n"
            f"  \\lean{{{node['name']}}}\\leanok{uses}\n"
            f"  {node['statement'] or escape(node['name'])}\n"
            f"\\end{{{kind}}}\n"
            f"\\begin{{proof}}{done}\n  {node['idea']}\n\\end{{proof}}\n"
        )
    return "\n".join(blocks)


def write(
    directory: Path, version: int, nodes: list[dict], problems=()
) -> None:
    """Write the blueprint files."""
    directory.mkdir(parents=True, exist_ok=True)
    plan = {"version": version, "nodes": nodes, "problems": list(problems)}
    write_atomic(
        directory / "blueprint.json", json.dumps(plan, ensure_ascii=False)
    )
    write_atomic(directory / "content.tex", latex(nodes))


def record_proved(directory: Path, statement: str) -> None:
    """Mark a proved statement in a run's blueprint, if it has one."""
    path = directory / "blueprint.json"
    key = statement_key(statement)
    with PLAN_LOCK:
        if path.exists():
            plan = read_record(path)
            for node in plan["nodes"]:
                if statement_key(node["lean"]) == key:
                    node["status"] = "proved"
            write_atomic(path, json.dumps(plan, ensure_ascii=False))


def from_summary(result_dir: Path) -> None:
    """Give a run without a blueprint one from its recorded plan, in Lean
    statements only: an imported run may predate blueprints."""
    summary = read_record(result_dir / "summary.json")
    sketch = summary.get("sketch") or {}
    if (result_dir / "blueprint").exists() or not sketch.get("lemmas"):
        return
    fields = summary["formalization"]
    names = ("definitions", "preamble", "theorem_name", "signature")
    blank = {"helpers": "", "proof": "", "refutation": ""}
    plan = SimpleNamespace(
        lemmas=[SimpleNamespace(**blank | lemma) for lemma in sketch["lemmas"]],
        main_proof=sketch.get("main_proof", ""),
    )
    nodes = plan_nodes(
        Formalization(*map(fields.get, names)),
        plan,
        {},
        summary.get("status") in ("proved", "verified_in_lean"),
    )
    write(result_dir / "blueprint", 1, nodes, ["no text"])


def run_metrics(result_dir: Path) -> dict:
    """The size of a run's proof and its measures, from its result files."""
    summary = read_record(result_dir / "summary.json")
    benchmark = read_record(result_dir / "benchmark.json")
    trace = read_record(result_dir / "trace.json")
    steps = trace.get("steps", [])
    lemmas = {}
    for step in steps:
        if step.get("event") == "lemma_proved":
            text = f"{step.get('helpers') or ''}\n{step.get('proof') or ''}"
            lemmas[step["lemma"]] = {
                "lines": sum(bool(line.strip()) for line in text.splitlines()),
                "characters": len(text),
                "prover": PROVERS.get(step.get("worker"), "AIProver"),
            }
    files = sorted(result_dir.glob("*_standalone.lean"))
    source = files[0].read_text() if files else ""
    proof = (trace.get("problem") or {}).get("informal_proof", "")
    return {
        "checks": summary.get("checks", {}),
        "review": (summary.get("review") or {}).get("verdict"),
        "size": {
            "characters": len(source),
            "lines": sum(bool(line.strip()) for line in source.splitlines()),
            "tokens": round(len(source) / CHARACTERS_PER_TOKEN),
            "informal_characters": len(proof),
            "expansion": round(len(source) / len(proof), 1) if proof else None,
        },
        "escalations": sum(s.get("event") == "lemma_escalation" for s in steps),
        "benchmark": benchmark,
        "lemmas": lemmas,
    }
