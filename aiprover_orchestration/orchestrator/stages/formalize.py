"""Formalization and blind audit.

The captain formalizes the informal result until it compiles; an auditor
writes a read-back of the Lean code without seeing the source; the captain
judges the read-back against the source. REVISE returns to formalization
with the read-back and the captain's issues as feedback.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Back-translation of formal statements checked against the
  source by an NLI model: Lean Workbook (Ying et al., NeurIPS 2024),
  https://github.com/InternLM/InternLM-Math/tree/main/leanworkbook
- [Similar] Automated alignment scoring of informal and formal statements:
  FormalAlign (Lu et al., ICLR 2025), https://github.com/rookie-joe/FormalAlign
"""

import logging

from ...lean.checker import forbidden_constructs
from ...lean.text import drop_imports
from .. import prompts
from ..structures import Formalization, extract_tag, parse_statement

logger = logging.getLogger(__name__)


class FormalizeStage:

    def formalize(
        self,
        form: Formalization | None = None,
        feedback: str = "",
        first_round: int = 0,
    ) -> Formalization:
        """Formalize and audit until FAITHFUL or the audit budget is spent.

        A resumed run passes a compiled but unaudited `form`, or the feedback
        of its last REVISE verdict, together with the next audit round.
        """
        for audit_round in range(first_round, self.config.max_audit_rounds + 1):
            if form is None:
                form = self._formalize_until_compiles(feedback)
            lean_code = (
                f"{form.definitions}\n\n{form.preamble}\n\n" f"{form.statement}"
            )
            form.readback = self.agents.complete(
                prompts.AUDITOR_TEMPLATE.format(
                    theorem_name=form.theorem_name, lean_code=lean_code
                ),
                system_prompt=self.auditor_system,
                role="auditor",
                phase=f"audit{audit_round}",
                round=audit_round,
            )
            reply = self._captain(
                prompts.JUDGE_READBACK_TEMPLATE.format(
                    informal_statement=self.row["informal_statement"],
                    informal_proof=self.row["informal_proof"],
                    lean_code=lean_code,
                    readback=form.readback,
                ),
                phase=f"judge{audit_round}",
            )
            form.verdict = extract_tag(reply, "verdict").upper()
            form.issues = extract_tag(reply, "issues")
            logger.info(f"audit round {audit_round}: verdict {form.verdict}")
            self._decision(
                "audit_verdict",
                round=audit_round,
                verdict=form.verdict,
                issues=form.issues,
                readback=form.readback,
            )
            if form.verdict.startswith("FAITHFUL"):
                return form
            feedback = prompts.FORMALIZE_FEEDBACK_TEMPLATE.format(
                definitions=self._own_definitions(form),
                statement=form.statement,
                problems="Auditor read-back:\n"
                + form.readback
                + "\n\nCaptain review:\n"
                + form.issues,
            )
            last_form, form = form, None
        return last_form

    def _formalize_until_compiles(self, feedback: str) -> Formalization:
        for repair in range(self.config.max_repairs + 1):
            template = (
                prompts.FORMALIZE_LIBRARY_TEMPLATE
                if self.library
                else prompts.FORMALIZE_TEMPLATE
            )
            reply = self._captain(
                template.format(
                    informal_statement=self.row["informal_statement"],
                    informal_proof=self.row["informal_proof"],
                    library=self.library,
                    theorem_name=self.slug,
                    feedback=feedback,
                ),
                phase=f"formalize{repair}",
            )
            definitions = drop_imports(extract_tag(reply, "definitions"))
            statement_block = drop_imports(extract_tag(reply, "statement"))
            problems = [
                f"forbidden construct in definitions: {name}"
                for name in forbidden_constructs(definitions)
            ]
            try:
                preamble, name, signature = parse_statement(statement_block)
                if name != self.slug:
                    problems.append(f"the theorem must be named `{self.slug}`")
            except ValueError as error:
                problems.append(str(error))
            if not problems:
                # The library is the prefix of the definitions, so every file
                # the run checks contains it.
                full = (
                    f"{self.library}\n\n{definitions}".strip()
                    if self.library
                    else definitions
                )
                form = Formalization(
                    full,
                    preamble,
                    name,
                    signature,
                    notes=extract_tag(reply, "notes"),
                )
                result = self._check(
                    "formalize",
                    self._standalone(form, form.statement),
                    repair=repair,
                )
                if result.ok:
                    logger.info(f"formalization compiles (repair {repair})")
                    self._decision(
                        "formalization_compiled",
                        repair=repair,
                        definitions=form.definitions,
                        preamble=preamble,
                        statement=form.statement,
                        notes=form.notes,
                        library=self.library_path,
                        library_chars=len(self.library),
                    )
                    return form
                problems.append("Lean errors:\n" + result.error_report())
            logger.info(
                f"formalization repair {repair}: " f"{problems[0][:200]}"
            )
            self._decision(
                "formalization_rejected", repair=repair, problems=problems
            )
            feedback = prompts.FORMALIZE_FEEDBACK_TEMPLATE.format(
                definitions=definitions,
                statement=statement_block,
                problems="\n".join(problems),
            )
        raise RuntimeError(
            "formalization did not compile within the repair budget"
        )
