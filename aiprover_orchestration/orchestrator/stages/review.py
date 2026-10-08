"""Independent review, LaTeX report and informalization.

A fresh reviewer referees the verified file against the source; a run is
proved only with verdict FAITHFUL. The writer then produces the report
(statements in mathematical notation beside verbatim Lean code) and the
informalization. Each result is recorded with the hash of the solution it
describes, so a resumed run reuses it instead of calling the models again.
"""

import hashlib
import logging
import subprocess
from pathlib import Path

from ...agents.base import AgentCallError
from ...lean.text import split_declarations
from .. import libraries, prompts
from ..config import MAIN_NAME
from ..reports import informal
from ..reports.report import ReportBuilder, escape
from ..structures import Formalization, Sketch, extract_tag

logger = logging.getLogger(__name__)

REVIEW_FIELDS = (
    "definitions_review",
    "statement_review",
    "proof_review",
    "concerns",
    "verdict",
    "model",
)
DOCUMENT_FIELDS = ("pdf", "compiled", "discrepancies", "problems")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ReviewStage:

    def _earlier_decision(self, event: str, solution: str) -> dict | None:
        """A decision of a previous session on the same solution, if any."""
        solution_digest = digest(solution)
        return next(
            (
                step
                for step in reversed(self.trace.document["steps"])
                if step.get("event") == event
                and step.get("solution_sha256") == solution_digest
            ),
            None,
        )

    def _earlier_document(self, event: str, solution: str) -> dict | None:
        """A report or informalization already written for `solution`."""
        earlier = self._earlier_decision(event, solution)
        if earlier and earlier.get("pdf") and Path(earlier["pdf"]).exists():
            return {key: earlier[key] for key in DOCUMENT_FIELDS}
        return None

    def _writer_or_reviewer(self, prompt: str, role: str, phase: str) -> str:
        system_prompt = (
            prompts.REVIEWER_SYSTEM
            if role == "reviewer"
            else prompts.WRITER_SYSTEM
        )
        return self.agents.complete(
            prompt, role=role, phase=phase, system_prompt=system_prompt
        )

    def final_review(self, form: Formalization, standalone: str) -> dict:
        """Independent referee of the verified file against the source."""
        earlier = self._earlier_decision("final_review", standalone)
        if earlier:
            logger.info(f"final review restored: {earlier['verdict']}")
            return {
                key: earlier[key] for key in earlier if key in REVIEW_FIELDS
            }
        reply = self.agents.complete(
            prompts.FINAL_REVIEW_TEMPLATE.format(
                informal_statement=self.row["informal_statement"],
                informal_proof=self.row["informal_proof"],
                target_statement=(
                    f"{form.definitions}\n\n{form.preamble}\n\n"
                    f"{form.statement}"
                ),
                lean_solution=standalone,
            ),
            role="reviewer",
            system_prompt=prompts.REVIEWER_SYSTEM,
            phase="review",
        )
        review = {
            key: extract_tag(reply, key).strip()
            for key in (
                "definitions_review",
                "statement_review",
                "proof_review",
                "concerns",
            )
        }
        review["verdict"] = (
            extract_tag(reply, "verdict").strip().upper() or "UNCERTAIN"
        )
        review["model"] = self.agents.agents["reviewer"].model
        logger.info(f"final review: verdict {review['verdict']}")
        self._decision(
            "final_review", solution_sha256=digest(standalone), **review
        )
        return review

    def credits(self, sketch: Sketch) -> dict[str, str]:
        """Who proved each declaration of the solution, from the trace."""
        steps = self.trace.document["steps"]
        solver_model = self.agents.agents["solver"].model
        credits = {}
        for lemma in sketch.lemmas:
            proved = next(
                (
                    step
                    for step in reversed(steps)
                    if step.get("event") == "lemma_proved"
                    and step.get("lemma") == lemma.name
                ),
                None,
            )
            if proved is None:
                continue
            if proved.get("worker") == "captain":
                text = "proved by the captain after a hand-back"
            else:
                jobs = [
                    step
                    for step in steps[: proved["index"]]
                    if step.get("kind") == "model_call"
                    and step.get("lemma") == lemma.name
                    and step.get("backend") == "aiprover"
                ]
                text = (
                    f"proved by AIProver, job {len(jobs)}, "
                    f"session {proved['worker']}"
                    if jobs
                    else f"proved by solver {proved['worker']} "
                    f"({solver_model}), "
                    f"round {proved.get('round', 0)}"
                )
            credits[lemma.name] = text
            for _, helper, _ in split_declarations(lemma.helpers):
                credits[helper] = f"helper of {lemma.name}, {text}"
        credits[MAIN_NAME] = "main proof from the captain's sketch"
        return credits

    def report(
        self,
        form: Formalization,
        sketch: Sketch,
        standalone: str,
        checks: dict,
        review: dict,
    ) -> dict:
        """LaTeX report and PDF in results/<run_id>/report."""
        earlier = self._earlier_document("report", standalone)
        if earlier:
            logger.info("report restored")
            return earlier
        environment = self.trace.document.get("lean_environment", {})
        axioms = ", ".join(
            f"\\texttt{{{escape(name)}}}" for name in checks.get("axioms") or []
        )
        source = self.row.get("source_subset") or self.row.get("source_name")
        agents = self.agents.agents
        summary_rows = [
            ("Source", escape(f"{source}, {self.row['uuid']}")),
            (
                "Lean",
                escape(
                    f"{environment.get('toolchain', '')}, Mathlib "
                    f"{environment.get('mathlib', '')[:10]}"
                ),
            ),
            (
                "Machine checks",
                "compiles; no \\texttt{sorry}; statement "
                "matches target; module build",
            ),
            ("Axioms", axioms),
            (
                "Independent review",
                escape(f"{review.get('verdict')} ({review.get('model')})"),
            ),
            (
                "Agents",
                escape(
                    f"captain {agents['captain'].model}; solvers "
                    f"{agents['solver'].model}; report "
                    f"{agents['writer'].model}"
                ),
            ),
            ("Run", f"\\texttt{{{escape(self.run_id)}}}"),
        ]
        library_names = sorted(
            libraries.declared_names(form.definitions[: self._library_chars()])
        )
        if library_names:
            summary_rows.insert(
                1,
                (
                    "Library",
                    escape(
                        f"{self.library_path}, {len(library_names)} declarations"
                    ),
                ),
            )
        builder = ReportBuilder(
            self.result_dir / "report",
            self.slug,
            self.row,
            form.theorem_name,
            form.statement,
            standalone,
            self.credits(sketch),
            summary_rows,
            review,
            library_names,
        )
        try:
            record = builder.build(self._writer_or_reviewer)
        except (OSError, subprocess.SubprocessError) as error:
            record = {
                "pdf": None,
                "compiled": False,
                "discrepancies": "",
                "problems": [f"report tooling failed: {error}"],
            }
        self._log_document("report", record)
        self._decision("report", solution_sha256=digest(standalone), **record)
        return record

    def informalize(
        self, form: Formalization, sketch: Sketch, standalone: str | None
    ) -> dict:
        """The result and every lemma in natural language (LaTeX and PDF in
        results/<run_id>/informal), written by the writer from the verified
        file, or from the sketch when lemmas remain unproved. The document
        is optional: a failure is recorded, not raised."""
        verified_file = standalone is not None
        lean_source = (
            standalone
            if verified_file
            else informal.sketch_source(
                form.definitions,
                form.statement,
                sketch.lemmas,
                sketch.main_proof,
            )
        )
        earlier = self._earlier_document("informal", lean_source)
        if earlier:
            logger.info("informalization restored")
            return earlier
        if self.agents.max_claude_calls:
            self.agents.max_claude_calls = max(
                self.agents.max_claude_calls,
                self.agents.claude_calls + self.config.informal_claude_calls,
            )
        lemmas = [(lemma.name, lemma.proved) for lemma in sketch.lemmas]
        builder = informal.InformalBuilder(
            self.result_dir / "informal",
            self.slug,
            self.row,
            form.theorem_name,
            lean_source,
            lemmas,
            informal.summary_rows(
                self.row,
                self.run_id,
                lemmas,
                verified_file,
                self.agents.agents["writer"].model,
            ),
        )
        try:
            record = builder.build(self._writer_or_reviewer)
        except (
            OSError,
            subprocess.SubprocessError,
            AgentCallError,
            RuntimeError,
        ) as error:
            record = {
                "pdf": None,
                "compiled": False,
                "discrepancies": "",
                "problems": [f"informalization failed: {error}"],
            }
        self._log_document("informalization", record)
        self._decision(
            "informal", solution_sha256=digest(lean_source), **record
        )
        return record

    @staticmethod
    def _log_document(name: str, record: dict) -> None:
        problems = record["problems"]
        logger.info(
            f"{name}: {'PDF written' if record['pdf'] else 'no PDF'}"
            + (f"; {len(problems)} problem(s)" if problems else "")
        )
