"""Informalization of a run: the result and every lemma of its formal proof,
stated and proved in natural mathematical language, as LaTeX and PDF.

The writer model turns the Lean source (the verified file, or for a run with
unproved lemmas the sketch with the proofs found) into a self-contained text:
the setting, the theorem, each lemma with its proof, and the proof of the
theorem from the lemmas. The text contains no Lean; a correspondence table
from lemma numbers to Lean declarations and their verification status is
generated, not written by the model. The reviewer checks every statement and
proof against the Lean source, and the writer corrects the discrepancies once.
Compile errors go back to the writer for repair, as for the report.

Informalize a finished run (writer and reviewer as recorded in its trace):
    python3 -m aiprover_orchestration.orchestrator.reports.informal \\
        results/<run_id>
Recompile a saved informalization without model calls:
    python3 -m aiprover_orchestration.orchestrator.reports.informal \\
        results/<run_id> --rebuild
"""

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from ...agents import AgentPool, build_agent
from ...paths import LOGS_DIR
from .. import prompts
from ..resume import restore_state
from .report import (
    FORBIDDEN_LATEX,
    PREAMBLE,
    MAX_COMPILE_REPAIRS,
    ReportText,
    compile_latex,
    escape,
    unicode_declarations,
)
from ..structures import extract_tag

INFORMAL_PREAMBLE = r"""\usepackage{longtable}
\newcommand{\unverified}{\quad\textup{\small[not machine-verified]}}
"""


class InformalBuilder:
    """Writes, checks and compiles the informalization of one run.

    `lemmas` lists the lemmas of the formal proof in order, as (Lean name,
    machine-verified)."""

    def __init__(
        self,
        out_dir: Path,
        slug: str,
        row: dict,
        theorem_name: str,
        lean_source: str,
        lemmas: list[tuple[str, bool]],
        summary_rows: list[tuple[str, str]],
    ):
        self.out_dir = Path(out_dir)
        self.slug = slug
        self.row = row
        self.theorem_name = theorem_name
        self.lean_source = lean_source
        self.lemmas = [tuple(lemma) for lemma in lemmas]
        self.summary_rows = [tuple(row) for row in summary_rows]
        self.name = f"{slug}_informal"

    def lemma_list(self) -> str:
        return "\n".join(
            f"{name} ({'verified' if verified else 'not verified'})"
            for name, verified in self.lemmas
        )

    def problems(self, text: ReportText) -> list[str]:
        """Contract violations of the writer's text, before compiling."""
        problems = []
        for part in (text.macros, text.body, text.title, text.abstract):
            problems += [
                f"forbidden construct `{m.group(0)}`"
                for m in FORBIDDEN_LATEX.finditer(part)
            ]
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

    def compile(self, document: str) -> tuple[bool, str]:
        return compile_latex(self.out_dir, self.name, document)

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
                )
            },
            "text": asdict(text),
        }
        (self.out_dir / "informal_parts.json").write_text(
            json.dumps(parts, indent=1, ensure_ascii=False)
        )

    # Writer loop -------------------------------------------------------------

    def build(self, complete: Callable[[str, str, str], str]) -> dict:
        """Write, check and compile; `complete(prompt, role, phase)` calls a
        model. Returns a record of the result."""
        record = {
            "pdf": None,
            "compiled": False,
            "discrepancies": "",
            "problems": [],
        }
        text = ReportText.parse(
            complete(
                prompts.INFORMAL_TEMPLATE.format(
                    informal_statement=self.row["informal_statement"],
                    informal_proof=self.row["informal_proof"],
                    lean_source=self.lean_source,
                    lemmas=self.lemma_list(),
                    theorem_name=self.theorem_name,
                ),
                "writer",
                "informal_write",
            )
        )
        text, ok, errors = self._compile_with_repairs(
            text, complete, "informal_repair"
        )
        record["compiled"] = ok
        if ok:
            check = complete(
                prompts.INFORMAL_CHECK_TEMPLATE.format(
                    lean_source=self.lean_source, body=text.body
                ),
                "reviewer",
                "informal_check",
            )
            discrepancies = extract_tag(check, "discrepancies").strip()
            record["discrepancies"] = discrepancies
            if discrepancies and discrepancies.lower().rstrip(".") != "none":
                revised = ReportText.parse(
                    complete(
                        prompts.REPORT_REVISE_TEMPLATE.format(
                            discrepancies=discrepancies, **asdict(text)
                        ),
                        "writer",
                        "informal_revise",
                    )
                )
                revised, revised_ok, _ = self._compile_with_repairs(
                    revised, complete, "informal_revise_repair"
                )
                if revised_ok:
                    text = revised
                else:
                    record["problems"].append(
                        "revision did not compile; the checked "
                        "version before revision was kept"
                    )
                    ok, errors = self.compile(self.document(text))
        self.save(text)
        record["problems"] += self.problems(text)
        if not ok:
            record["problems"].append(errors[:2000])
        pdf = self.out_dir / f"{self.name}.pdf"
        if ok and pdf.exists():
            record["pdf"] = str(pdf)
        return record

    def _compile_with_repairs(self, text: ReportText, complete, phase: str):
        errors = ""
        for repair in range(MAX_COMPILE_REPAIRS + 1):
            problems = self.problems(text)
            if not problems:
                ok, errors = self.compile(self.document(text))
                if ok:
                    return text, True, ""
                problems = [errors]
            if repair == MAX_COMPILE_REPAIRS:
                return text, False, "\n".join(problems)
            text = ReportText.parse(
                complete(
                    prompts.REPORT_REPAIR_TEMPLATE.format(
                        errors="\n".join(problems)[:6000], **asdict(text)
                    ),
                    "writer",
                    f"{phase}{repair}",
                )
            )
        return text, False, errors


def sketch_source(
    definitions: str, statement: str, lemmas, main_proof: str
) -> str:
    """Lean source of a run whose lemmas are not all proved: the definitions,
    the target, each lemma with its proof (or `sorry`) and the main proof."""
    parts = [definitions.strip(), statement.strip()]
    for lemma in lemmas:
        proof = (
            lemma.proof.strip() if lemma.proved else "sorry  -- not verified"
        )
        parts.append(
            (
                lemma.helpers.strip() + "\n\n"
                if lemma.proved and lemma.helpers.strip()
                else ""
            )
            + f"{lemma.statement} := by\n  {proof}"
        )
    parts.append(f"-- main proof\n{main_proof.strip()}")
    return "\n\n".join(parts)


def summary_rows(
    row: dict,
    run_id: str,
    lemmas: list[tuple[str, bool]],
    verified_file: bool,
    writer: str,
) -> list[tuple[str, str]]:
    proved = sum(verified for _, verified in lemmas)
    status = (
        "proof machine-verified (Lean 4, Mathlib)"
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


def informalize_run(result_dir: Path) -> dict:
    """Informalize a finished run from its trace, with the writer and reviewer
    the trace records; calls are appended to logs/<run_id>/calls.jsonl."""
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
    verified_file = bool(standalone) and all(
        lemma.proved for lemma in sketch.lemmas
    )
    lean_source = (
        standalone[0].read_text()
        if verified_file
        else sketch_source(
            form.definitions, form.statement, sketch.lemmas, sketch.main_proof
        )
    )
    lemmas = [(lemma.name, lemma.proved) for lemma in sketch.lemmas]
    specs = trace["agents"]
    roles = {
        role: build_agent(specs.get(role) or specs["captain"])
        for role in ("writer", "reviewer")
    }
    pool = AgentPool(roles, LOGS_DIR / run_id / "calls.jsonl")
    systems = {
        "writer": prompts.WRITER_SYSTEM,
        "reviewer": prompts.REVIEWER_SYSTEM,
    }

    def complete(prompt: str, role: str, phase: str) -> str:
        return pool.complete(
            prompt, role=role, phase=phase, system_prompt=systems[role]
        )

    builder = InformalBuilder(
        result_dir / "informal",
        slug,
        trace["problem"],
        form.theorem_name,
        lean_source,
        lemmas,
        summary_rows(
            trace["problem"],
            run_id,
            lemmas,
            verified_file,
            roles["writer"].model,
        ),
    )
    return builder.build(complete)


def rebuild(out_dir: Path) -> None:
    """Recompile a saved informalization without model calls."""
    parts = json.loads((out_dir / "informal_parts.json").read_text())
    builder = InformalBuilder(out_dir, **parts["builder"])
    text = ReportText(**parts["text"])
    print("\n".join(builder.problems(text)) or "contract: ok")
    ok, errors = builder.compile(builder.document(text))
    print("compiled" if ok else errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    arguments = parser.parse_args()
    if arguments.rebuild:
        rebuild(arguments.result_dir / "informal")
    else:
        print(json.dumps(informalize_run(arguments.result_dir), indent=1))
