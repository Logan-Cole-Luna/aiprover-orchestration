"""Informalization of a run: the result and every lemma of its formal proof,
stated and proved in natural mathematical language, as LaTeX and PDF.

The writer model turns the Lean source (the verified file, for a run with
unproved lemmas the sketch with the proofs found, or for a disproved run the
refutation file) into a self-contained text:
the setting, the theorem, each lemma with its proof, and the proof of the
theorem from the lemmas. The text contains no Lean; a correspondence table
from lemma numbers to Lean declarations and their verification status is
generated, not written by the model. The reviewer checks every statement and
proof against the Lean source, and the writer corrects the discrepancies once.
Compile errors go back to the writer for repair, as for the report.

Informalize a finished run (writer and reviewer as recorded in its trace):
    python3 -m aiprover_orchestration.orchestrator.reports.informal \\
        results/<run_id>
Reuse the writer's draft recorded in the trace (`--reuse-draft`); the check
and the revision are new calls:
    python3 -m aiprover_orchestration.orchestrator.reports.informal \\
        results/<run_id> --reuse-draft
Each informalization also writes results/<run_id>/<slug>_result.jsonl: the run
as a row of the problem datasets (data/*.jsonl), with the informalization's
statement and proof and the Lean source.

Recompile a saved informalization and rewrite the row without model calls:
    python3 -m aiprover_orchestration.orchestrator.reports.informal \\
        results/<run_id> --rebuild
"""

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

from ...agents import AgentPool, build_agent, role_spec
from ...lean.text import declaration_names, indent
from ..config import MAIN_NAME
from ...paths import LOGS_DIR
from .. import prompts
from ..resume import restore_state
from .report import (
    PREAMBLE,
    LatexBuilder,
    ReportText,
    escape,
    forbidden_latex,
    unicode_declarations,
)

INFORMAL_PREAMBLE = r"""\usepackage{longtable}
\newcommand{\unverified}{\quad\textup{\small[not machine-verified]}}
"""
# The run's result in the format of the problem datasets, at the run's root.
RESULT_ROW_SUFFIX = "_result.jsonl"


class InformalBuilder(LatexBuilder):
    """Writes, checks and compiles the informalization of one run.

    `lemmas` lists the lemmas of the formal proof in order, as (Lean name,
    machine-verified). With `refuted`, the Lean source is a refutation and
    `theorem_name` its negation of the claim."""

    phase = "informal"

    def __init__(
        self,
        out_dir: Path,
        slug: str,
        row: dict,
        theorem_name: str,
        lean_source: str,
        lemmas: list[tuple[str, bool]],
        summary_rows: list[tuple[str, str]],
        refuted: bool = False,
    ):
        self.out_dir = Path(out_dir)
        self.slug = slug
        self.row = row
        self.theorem_name = theorem_name
        self.lean_source = lean_source
        self.lemmas = [tuple(lemma) for lemma in lemmas]
        self.summary_rows = [tuple(row) for row in summary_rows]
        self.refuted = refuted
        self.name = f"{slug}_informal"

    def lemma_list(self) -> str:
        return "\n".join(
            f"{name} ({'verified' if verified else 'not verified'})"
            for name, verified in self.lemmas
        )

    def problems(self, text: ReportText) -> list[str]:
        """Contract violations of the writer's text, before compiling."""
        problems = forbidden_latex(text)
        labels = re.findall(r"\\label\{lem:([^}]*)\}", text.body)
        names = [name for name, _ in self.lemmas]
        missing = [name for name in names if name not in labels]
        unknown = sorted(set(labels) - set(names))
        repeated = sorted({name for name in labels if labels.count(name) > 1})
        if missing:
            problems.append(
                f"lemmas without \\label{{lem:NAME}}: {', '.join(missing)}"
            )
        if unknown:
            problems.append(
                f"lemma labels not in the lemma list: {', '.join(unknown)}"
            )
        if repeated:
            problems.append(
                f"lemmas stated more than once: {', '.join(repeated)}"
            )
        if "\\label{thm:main}" not in text.body:
            problems.append("no theorem labelled thm:main")
        if not text.body.strip():
            problems.append("empty <body>")
        return problems

    # Document --------------------------------------------------------------

    def summary_table(self) -> str:
        rows = "\n".join(
            f"{escape(key)} & {value} \\\\" for key, value in self.summary_rows
        )
        return (
            "\\begin{center}\\small\n\\begin{tabular}{@{}ll@{}}\n\\toprule\n"
            f"{rows}\n\\bottomrule\n\\end{{tabular}}\n\\end{{center}}"
        )

    def correspondence(self) -> str:
        """Lemma numbers, Lean names and status, generated from the lemma list."""
        rows = "\n".join(
            f"\\cref{{lem:{name}}} & \\texttt{{{escape(name)}}} & "
            f"{'verified' if verified else 'not verified'} \\\\"
            for name, verified in self.lemmas
        )
        return (
            "\\appendix\n\\section{Correspondence with the formal proof}\n"
            "Each lemma of the text and the Lean declaration that states it; "
            f"\\cref{{thm:main}} is \\texttt{{{escape(self.theorem_name)}}}.\n\n"
            "\\begin{longtable}{@{}lll@{}}\n\\toprule\nLemma & Lean declaration & Status \\\\\n"
            f"\\midrule\n\\endhead\n{rows}\n\\bottomrule\n\\end{{longtable}}"
        )

    def document(self, text: ReportText) -> str:
        unicode, _ = unicode_declarations(
            text.body + text.abstract + text.title + text.macros
        )
        return (
            f"{PREAMBLE}{INFORMAL_PREAMBLE}{unicode}\n{text.macros}\n\n"
            f"\\title{{{text.title}}}\n\\date{{}}\n\\begin{{document}}\n\\maketitle\n"
            f"\\begin{{abstract}}\n{text.abstract}\n\\end{{abstract}}\n\n"
            f"{self.summary_table()}\n\n\\tableofcontents\n\n{text.body}\n\n"
            f"{self.correspondence()}\n\\end{{document}}\n"
        )

    def documents(self, text: ReportText) -> list[str]:
        return [self.document(text)]

    def write_prompt(self) -> str:
        template = (
            prompts.INFORMAL_REFUTATION_TEMPLATE
            if self.refuted
            else prompts.INFORMAL_TEMPLATE
        )
        return template.format(
            informal_statement=self.row["informal_statement"],
            informal_proof=self.row["informal_proof"],
            lean_source=self.lean_source,
            lemmas=self.lemma_list(),
            theorem_name=self.theorem_name,
        )

    def check_prompt(self, text: ReportText) -> str:
        return prompts.INFORMAL_CHECK_TEMPLATE.format(
            lean_source=self.lean_source, body=text.body
        )

    def save(self, text: ReportText) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        parts = {
            "builder": {
                key: getattr(self, key)
                for key in (
                    "slug",
                    "row",
                    "theorem_name",
                    "lean_source",
                    "lemmas",
                    "summary_rows",
                    "refuted",
                )
            },
            "text": asdict(text),
        }
        (self.out_dir / "informal_parts.json").write_text(
            json.dumps(parts, indent=1, ensure_ascii=False)
        )
        self.save_result_row(text)

    def result_row(self, text: ReportText) -> dict:
        """The run as a row of the problem datasets (data/*.jsonl): the
        problem's fields, with the statement and proof of the informalization
        in place of the source's, and the Lean source it was checked against."""
        body = text.body.strip()
        label = body.find("\\label{thm:main}")
        end = body.find("\\end{theorem}", label) if label >= 0 else -1
        split = end + len("\\end{theorem}") if end >= 0 else 0
        row = {
            key: value
            for key, value in self.row.items()
            if not key.startswith("_")
        }
        return row | {
            "informal_statement": body[:split].strip(),
            "informal_proof": body[split:].strip(),
            "latex_macros": text.macros.strip(),
            "formal_proof": self.lean_source,
            "machine_verified": all(verified for _, verified in self.lemmas),
        }

    def save_result_row(self, text: ReportText) -> None:
        """Write the result row to <run>/<slug>_result.jsonl."""
        (self.out_dir.parent / f"{self.slug}{RESULT_ROW_SUFFIX}").write_text(
            json.dumps(self.result_row(text), ensure_ascii=False) + "\n"
        )

def sketch_source(
    definitions: str, statement: str, lemmas, main_proof: str
) -> str:
    """Lean source of a run whose lemmas are not all proved: the definitions,
    the target, each lemma with its proof (or `sorry`) and the main proof."""
    parts = [definitions.strip(), statement.strip()]
    for lemma in lemmas:
        proof = lemma.proof if lemma.proved else "sorry  -- not verified"
        parts.append(
            (
                lemma.helpers.strip() + "\n\n"
                if lemma.proved and lemma.helpers.strip()
                else ""
            )
            + f"{lemma.statement} := by\n{indent(proof)}"
        )
    parts.append(f"-- main proof\n{main_proof.strip()}")
    return "\n\n".join(parts)


def summary_rows(
    row: dict,
    run_id: str,
    lemmas: list[tuple[str, bool]],
    verified_file: bool,
    writer: str,
    refuted: bool = False,
) -> list[tuple[str, str]]:
    proved = sum(verified for _, verified in lemmas)
    status = (
        "claim disproved; the disproof is machine-verified (Lean 4, Mathlib)"
        if refuted
        else "proof machine-verified (Lean 4, Mathlib)"
        if verified_file
        else f"{proved} of {len(lemmas)} lemmas machine-verified"
    )
    return [
        (
            "Source",
            escape(
                f"{row.get('source_subset') or row.get('source_name')}, {row['uuid']}"
            ),
        ),
        ("Status", escape(status)),
        ("Lemmas", str(len(lemmas))),
        (
            "Text",
            escape(f"written by {writer}, checked against the Lean source"),
        ),
        ("Run", f"\\texttt{{{escape(run_id)}}}"),
    ]


def refutation_parts(lean_source: str) -> tuple[str, list[tuple[str, bool]]]:
    """The negation's name in a refutation file and its helper lemmas, all
    verified: the theorems other than the claim and its negation."""
    negation = f"{MAIN_NAME}_refuted"
    lemmas = [
        (name, True)
        for name in declaration_names(lean_source, ("theorem", "lemma"))
        if name not in (MAIN_NAME, negation)
        and not name.startswith("setting_")
    ]
    return negation, lemmas


def informalize_run(result_dir: Path, reuse_draft: bool = False) -> dict:
    """Informalize a finished run from its trace, with the writer and reviewer
    the trace records; calls are appended to logs/<run_id>/calls.jsonl. With
    `reuse_draft`, the writer's last draft in the trace replaces a new one."""
    trace = json.loads((result_dir / "trace.json").read_text())
    run_id = trace["run_id"]
    state = restore_state(trace)
    if state.formalization is None or state.sketch is None:
        raise SystemExit(f"{run_id} has no proof sketch to informalize")
    form, sketch = state.formalization, state.sketch
    summary_path = result_dir / "summary.json"
    summary = (
        json.loads(summary_path.read_text()) if summary_path.exists() else {}
    )
    slug = summary.get("slug") or form.theorem_name
    standalone = sorted(result_dir.glob("*_standalone.lean"))
    refutation = sorted(result_dir.glob("*_refutation.lean"))
    refuted = bool(sketch.refutation and refutation)
    verified_file = bool(standalone) and all(
        lemma.proved for lemma in sketch.lemmas
    )
    theorem_name = form.theorem_name
    lemmas = [(lemma.name, lemma.proved) for lemma in sketch.lemmas]
    if refuted:
        lean_source = refutation[0].read_text()
        theorem_name, lemmas = refutation_parts(lean_source)
    elif verified_file:
        lean_source = standalone[0].read_text()
    else:
        lean_source = sketch_source(
            form.definitions, form.statement, sketch.lemmas, sketch.main_proof
        )
    specs = trace["agents"]
    captain = {
        key: value for key, value in specs["captain"].items() if key != "tools"
    }
    roles = {
        role: build_agent(role_spec(role, specs.get(role) or captain))
        for role in ("writer", "reviewer")
    }
    pool = AgentPool(roles, LOGS_DIR / run_id / "calls.jsonl")
    systems = {
        "writer": prompts.WRITER_SYSTEM,
        "reviewer": prompts.REVIEWER_SYSTEM,
    }

    drafts = [
        step["response"]
        for step in trace["steps"]
        if step["kind"] == "model_call"
        and step.get("label") == "informal_write"
        and step.get("response")
    ]

    def complete(prompt: str, role: str, phase: str) -> str:
        if reuse_draft and drafts and phase == "informal_write":
            return drafts[-1]
        return pool.complete(
            prompt, role=role, phase=phase, system_prompt=systems[role]
        )

    builder = InformalBuilder(
        result_dir / "informal",
        slug,
        trace["problem"],
        theorem_name,
        lean_source,
        lemmas,
        summary_rows(
            trace["problem"],
            run_id,
            lemmas,
            verified_file,
            roles["writer"].model,
            refuted,
        ),
        refuted,
    )
    return builder.build(complete)


def rebuild(out_dir: Path) -> None:
    """Recompile a saved informalization and rewrite its result row, without
    model calls."""
    parts = json.loads((out_dir / "informal_parts.json").read_text())
    builder = InformalBuilder(out_dir, **parts["builder"])
    text = ReportText(**parts["text"])
    builder.save_result_row(text)
    print("\n".join(builder.problems(text)) or "contract: ok")
    ok, errors = builder.compile(builder.document(text))
    print("compiled" if ok else errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument(
        "--reuse-draft",
        action="store_true",
        help="use the writer's last draft in the trace instead of a new one",
    )
    arguments = parser.parse_args()
    if arguments.rebuild:
        rebuild(arguments.result_dir / "informal")
    else:
        record = informalize_run(arguments.result_dir, arguments.reuse_draft)
        print(json.dumps(record, indent=1))
