"""Formalization and blind audit.

The captain formalizes the informal result until it compiles; Lean probes
the statement (fidelity.py), and a proof of `False` from its hypotheses
returns to formalization at once. An auditor writes a read-back of the Lean
code without seeing the source, and each judge compares the read-back and
the Lean evidence with the source: first the `judge` role, a model other
than the captain's when configured, then the captain. Every judge must
return FAITHFUL. REVISE returns to formalization with the read-back and the
issues as feedback. The optional `formalizer` role writes a reference
formalization of the source, without seeing the captain's, which Lean
compares with the statement.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Back-translation of formal statements checked against the
  source by an NLI model: Lean Workbook (Ying et al., NeurIPS 2024),
  https://github.com/InternLM/InternLM-Math/tree/main/leanworkbook
- [Similar] Automated alignment scoring of informal and formal statements:
  FormalAlign (Lu et al., ICLR 2025), https://github.com/rookie-joe/FormalAlign
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from ...agents.base import AgentCallError, AgentLimitReached, BudgetExhausted
from ...lean.checker import forbidden_problems
from ...lean.text import drop_imports
from .. import fidelity, prompts
from ..config import FILE_HEADER
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
        if first_round > self.config.max_audit_rounds:
            raise ValueError(
                f"audit round {first_round} exceeds max_audit_rounds="
                f"{self.config.max_audit_rounds}"
            )
        for audit_round in range(first_round, self.config.max_audit_rounds + 1):
            if form is None:
                form = self._formalize_until_compiles(feedback)
            lean_code = (
                f"{form.definitions}\n\n{form.preamble}\n\n" f"{form.statement}"
            )
            evidence = self._fidelity_evidence(form)
            form.evidence = evidence.report()
            if evidence.vacuous:
                form.readback = ""
                form.verdict = "REVISE"
                form.issues = (
                    "Lean proved False from the statement's hypotheses, so "
                    "the statement is vacuous."
                )
            else:
                form.readback = self.agents.complete(
                    prompts.AUDITOR_TEMPLATE.format(
                        theorem_name=form.theorem_name, lean_code=lean_code
                    ),
                    system_prompt=self.auditor_system,
                    role="auditor",
                    phase=f"audit{audit_round}",
                    round=audit_round,
                )
                self._judge(form, lean_code, audit_round)
            logger.info(f"audit round {audit_round}: verdict {form.verdict}")
            self._decision(
                "audit_verdict",
                round=audit_round,
                verdict=form.verdict,
                issues=form.issues,
                readback=form.readback,
                evidence=form.evidence,
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

    def _judge(
        self, form: Formalization, lean_code: str, audit_round: int
    ) -> None:
        """Set the verdict of the panel: the `judge` role, if configured, and
        the captain. The first REVISE decides, which spares the later judge."""
        prompt = prompts.JUDGE_READBACK_TEMPLATE.format(
            informal_statement=self.row["informal_statement"],
            informal_proof=self.row["informal_proof"],
            lean_code=lean_code,
            readback=form.readback,
            evidence=form.evidence,
        )
        roles = ["captain"]
        if "judge" in self.agents.agents:
            roles.insert(0, "judge")
        for role in roles:
            reply = self.agents.complete(
                prompt,
                role=role,
                phase=f"judge{audit_round}",
                system_prompt=prompts.JUDGE_SYSTEM,
            )
            form.verdict = extract_tag(reply, "verdict").upper()
            form.issues = extract_tag(reply, "issues")
            if not form.verdict.startswith("FAITHFUL"):
                return

    def _fidelity_evidence(self, form: Formalization) -> fidelity.Evidence:
        """What Lean establishes about the statement. Probes are skipped when
        `fidelity_probes` is off, and a failed probe proves nothing."""
        evidence = fidelity.Evidence()
        if not self.config.fidelity_probes:
            return evidence
        name, signature = form.theorem_name, form.signature
        files = {
            label: self._standalone(form, probe(name, signature))
            for label, probe in (
                ("vacuous", fidelity.vacuity_probe),
                ("automatic", fidelity.automation_probe),
                ("tested", fidelity.counterexample_probe),
            )
        }
        reference = self._reference_formalization()
        if reference:
            statement, other = map(self._premise, (form, reference))
            prefix = f"{FILE_HEADER}{self.library}\n\n"
            files["implies_reference"] = prefix + fidelity.implication_file(statement, other)
            files["implied_by_reference"] = prefix + fidelity.implication_file(other, statement)
        # The checks are independent, so they run at once.
        with ThreadPoolExecutor(self.config.lean_parallel) as pool:
            results = dict(zip(files, pool.map(
                lambda item: self._check(f"fidelity_{item[0]}", item[1]), files.items()
            )))
        evidence.vacuous = results["vacuous"].ok
        evidence.automatic = results["automatic"].ok
        evidence.counterexample = fidelity.counterexample_in(results["tested"].errors)
        if reference:
            evidence.implies_reference = fidelity.used_premise(
                results["implies_reference"], reference.theorem_name
            )
            evidence.implied_by_reference = fidelity.used_premise(
                results["implied_by_reference"], form.theorem_name
            )
        return evidence

    def _premise(self, form: Formalization) -> tuple[str, str, str, str]:
        return (
            self._own_definitions(form),
            form.preamble,
            form.theorem_name,
            form.signature,
        )

    def _reference_formalization(self) -> Formalization | None:
        """The `formalizer` role's formalization of the source, written once.
        Without one, the verdict rests on the judges."""
        if "formalizer" not in self.agents.agents:
            return None
        if not self.reference_attempted:
            self.reference_attempted = True
            try:
                self.reference = self._formalize_until_compiles(
                    "", "formalizer"
                )
            # An outage stops the run for a resume, as anywhere else.
            except (AgentCallError, AgentLimitReached, BudgetExhausted):
                raise
            except RuntimeError as error:
                logger.info(f"no reference formalization: {error}")
        return self.reference

    def _formalize_until_compiles(
        self, feedback: str, role: str = "captain"
    ) -> Formalization:
        """A compiling formalization by `role`. Only the captain's enters
        the run's state: the reference is recorded under its own events."""
        event = "formalization" if role == "captain" else "reference"
        for repair in range(self.config.max_repairs + 1):
            template = (
                prompts.FORMALIZE_LIBRARY_TEMPLATE
                if self.library
                else prompts.FORMALIZE_TEMPLATE
            )
            reply = self.agents.complete(
                template.format(
                    informal_statement=self.row["informal_statement"],
                    informal_proof=self.row["informal_proof"],
                    library=self.library,
                    theorem_name=self.slug,
                    feedback=feedback,
                ),
                role=role,
                phase=f"formalize{repair}",
                system_prompt=prompts.CAPTAIN_SYSTEM,
            )
            definitions = drop_imports(extract_tag(reply, "definitions"))
            statement_block = drop_imports(extract_tag(reply, "statement"))
            problems = forbidden_problems(definitions, "definitions")
            problems += forbidden_problems(
                statement_block, "statement", statements=True
            )
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
                        f"{event}_compiled",
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
                f"{event} repair {repair}: " f"{problems[0][:200]}"
            )
            self._decision(
                f"{event}_rejected", repair=repair, problems=problems
            )
            feedback = prompts.FORMALIZE_FEEDBACK_TEMPLATE.format(
                definitions=definitions,
                statement=statement_block,
                problems="\n".join(problems),
            )
        raise RuntimeError(
            "formalization did not compile within the repair budget"
        )
