"""Vacuity check, proof sketch, refutation and replanning.

Before planning, the orchestrator tests the setting: facts it proves about it
(a degenerate structure, contradictory hypotheses, a small case) join the
definitions. The orchestrator then decomposes the proof into lemmas (stated in
full, left as `sorry`) and a proof of `solution` from them; the sketch must
compile. After failed lemmas, the orchestrator revises the sketch with the
provers' failed code and errors; proved lemmas whose statements are
unchanged keep their proofs. A statement the orchestrator judges false, the
theorem or a lemma, counts as refuted only when Lean accepts a proof of its
negation from the definitions with the standard axioms.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Informal proof to a formal sketch with `sorry` gaps closed by a
  prover: Draft, Sketch, and Prove (Jiang et al., ICLR 2023),
  https://github.com/albertqjiang/draft_sketch_prove
- [Similar] Informal planner decomposing into subgoals for a Lean prover
  model: Hilbert (Varambally et al., ICLR 2026),
  https://github.com/Rose-STL-Lab/ml-hilbert
- [Similar] Lemmas following the dependency graph of the informal proof:
  ProofFlow (Cabral et al., ICLR 2026),
  https://github.com/Huawei-AI4Math/ProofFlow
- [Similar] Revising the global plan from failed lemmas: Goedel-Architect
  (Chung et al., ICML 2026; no code released),
  https://icml.cc/virtual/2026/82541
- [Similar] Refutation accepted only with a negation proof or a countermodel:
  MAESTRO §3.4 (docs/papers/34453_Maestro_Agentic_Formal_P.pdf); goal and
  negation attempted together: HERMES, https://arxiv.org/abs/2511.18760
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from ...agents.base import AgentCallError, AgentLimitReached
from ...lean.checker import forbidden_problems, nonstandard_axioms
from ...lean.text import (
    declaration_names,
    drop_imports,
    indent,
    normalize,
    split_declarations,
)
from .. import prompts
from ..config import MAIN_NAME

# Lean's `plausible` reports a falsified statement with this line.
COUNTEREXAMPLE = "Found a counter-example!"
COUNTEREXAMPLE_SECONDS = 60
from ..structures import (
    Formalization,
    Lemma,
    Sketch,
    carry_definitions,
    declared_names,
    definition_blocks,
    definitions_changed,
    dependency_order,
    extract_named,
    extract_tag,
    extract_tactics,
    failed_lemma_text,
    missing_lemmas,
    new_definitions,
    parse_lemmas,
    refers_to,
    reinstated,
    sketch_definitions,
    statement_key,
    statements,
    stub_listing,
    unchanged_names,
)

logger = logging.getLogger(__name__)

# Facts proved by the vacuity check, which join the definitions.
SETTING_PREFIX = "setting_"


class SketchStage:

    def _refutation(
        self,
        form: Formalization,
        label: str,
        statement: str,
        name: str,
        helpers: str,
        proof: str,
        **trace_fields,
    ) -> tuple[str, list[str]]:
        """Lean text proving the negation of `statement` (`theorem name
        ...`, without proof) and the problems that reject it. It is checked
        against the definitions alone, so that it rests on no unproved
        lemma, and must compile with the standard axioms only."""
        refuted = f"{name}_refuted"
        text = "\n\n".join(
            filter(
                None,
                [
                    helpers,
                    f"theorem {refuted} : ¬ (type_of% @{name}) := by\n"
                    f"{indent(proof)}",
                ],
            )
        )
        problems = forbidden_problems(text)
        if problems:
            return text, problems
        result = self._check(
            label,
            self._standalone(form, f"{statement} := by sorry\n\n{text}")
            + f"\n#print axioms {refuted}\n",
            **trace_fields,
        )
        if not result.ok:
            return text, [result.error_report()]
        extra_axioms = nonstandard_axioms(result, refuted)
        if extra_axioms != []:
            return text, [
                f"the refutation must depend on the standard axioms only; "
                f"it depends on {extra_axioms}"
            ]
        return text, []

    def check_vacuity(self, form: Formalization) -> str:
        """Facts about the setting that the orchestrator proves before planning:
        Lean theorems named `setting_...`, compiled against the definitions
        without `sorry`. Empty when the orchestrator finds none."""
        feedback, facts = "", ""
        for repair in range(2):
            reply = self._orchestrator(
                prompts.VACUITY_CHECK_TEMPLATE.format(
                    definitions=form.definitions,
                    preamble=form.preamble,
                    signature=form.signature,
                    informal_proof=self.row["informal_proof"],
                    feedback=feedback,
                ),
                phase=f"setting{repair}",
            )
            facts = drop_imports(extract_tag(reply, "facts"))
            if not facts:
                break
            names = declaration_names(facts, ("theorem", "lemma"))
            problems = forbidden_problems(facts)
            problems += [
                f"`{name}` must be named `{SETTING_PREFIX}...`"
                for name in names
                if not name.startswith(SETTING_PREFIX)
            ]
            if not names:
                problems.append("<facts> holds no theorem")
            if not problems:
                result = self._check(
                    "setting",
                    self._standalone(form, facts)
                    + "".join(f"\n#print axioms {name}" for name in names)
                    + "\n",
                    repair=repair,
                )
                extra_axioms = sorted(
                    {
                        axiom
                        for name in names
                        for axiom in nonstandard_axioms(result, name) or []
                    }
                )
                if result.ok and not extra_axioms:
                    logger.info(f"setting facts proved: {names}")
                    break
                problems.append(
                    result.error_report()
                    if not result.ok
                    else f"the facts depend on {extra_axioms}"
                )
            logger.info(f"vacuity check repair {repair}: {problems[0][:200]}")
            feedback = prompts.VACUITY_CHECK_REPAIR_TEMPLATE.format(
                facts=facts, errors="\n".join(problems)[:6000]
            )
            facts = ""
        self._decision("vacuity_checked", facts=facts)
        return facts

    def sketch(
        self,
        form: Formalization,
        replan_feedback: str = "",
        excluded: dict[str, str] | None = None,
        earlier: list[Lemma] = (),
    ) -> Sketch:
        """A compiled sketch, or a compiled refutation of the theorem.
        `excluded` maps statements a sketch may not state again
        (`statement_key`: refuted, or with the attempt budget spent) to the
        reason. Lemmas may use the split definitions of the `earlier`
        sketch's lemmas. If the budget ends without an accepted sketch, the last
        compiled one is accepted."""
        feedback = replan_feedback
        compiled = None
        # The judge's objections to the accepted sketch, if it never accepted
        # one (the pipeline may then pass to the next approach).
        self.sketch_rejected = ""
        # The judge's blocking objections to the previous version, if any.
        self._judged = ""
        gaps_note = prompts.GAPS_SKETCH_TEMPLATE if self.config.open_subgoals else ""
        for repair in range(self.config.max_repairs + 1):
            try:
                reply = self._orchestrator(
                    prompts.SKETCH_TEMPLATE.format(
                        definitions=form.definitions,
                        preamble=form.preamble,
                        signature=form.signature,
                        informal_proof=self.row["informal_proof"],
                        feedback=gaps_note + feedback,
                    ),
                    phase=f"sketch{repair}",
                )
            except AgentLimitReached as error:
                # The same prompt would reach the limit again.
                self._decision(
                    "sketch_rejected", repair=repair, problems=[str(error)]
                )
                break
            refutation = extract_tactics(reply, "refutation")
            if refutation:
                text, problems = self._refutation(
                    form,
                    "refutation",
                    f"theorem {MAIN_NAME}{form.signature}",
                    MAIN_NAME,
                    drop_imports(extract_tag(reply, "helpers")),
                    refutation,
                    repair=repair,
                )
                if not problems:
                    logger.info("the theorem is refuted")
                    self._decision(
                        "theorem_refuted", repair=repair, refutation=text
                    )
                    return Sketch([], "", refutation=text)
                logger.info(f"refutation repair {repair}: {problems[0][:200]}")
                self._decision(
                    "sketch_rejected", repair=repair, problems=problems
                )
                feedback = (
                    replan_feedback
                    + prompts.REFUTATION_REPAIR_TEMPLATE.format(
                        refutation=text, errors="\n".join(problems)[:6000]
                    )
                )
                continue
            lemma_block = drop_imports(extract_tag(reply, "lemmas"))
            main_proof = extract_tactics(reply, "main_proof")
            lemmas = parse_lemmas(lemma_block)
            definitions = sketch_definitions(lemma_block)
            carry_definitions(lemmas, earlier, definitions)
            names = [lemma.name for lemma in lemmas]
            problems = [
                f"lemma `{name}` must be stated with `:= by sorry`"
                for kind, name, _ in split_declarations(lemma_block)
                if kind in ("theorem", "lemma") and name not in names
            ]
            used = set(definition_blocks(lemmas))
            problems += [
                f"the main proof uses `{name}`, which no lemma statement "
                f"uses, so it is not in the file"
                for block in definitions
                if block not in used
                for name in declared_names(block)
                if refers_to(main_proof, name)
            ]
            problems += forbidden_problems(main_proof, "main proof")
            problems += forbidden_problems(
                lemma_block, "lemma statements", statements=True
            )
            problems += forbidden_problems(
                "\n\n".join(definitions), "definitions"
            )
            if len(set(names)) != len(names) or {
                MAIN_NAME,
                form.theorem_name,
            } & set(names):
                problems.append(
                    f"lemma names must be unique and differ "
                    f"from `{MAIN_NAME}` and "
                    f"`{form.theorem_name}`"
                )
            if not main_proof:
                problems.append("empty main proof")
            problems += [
                f"lemma `{lemma.name}` states a proposition that "
                f"{excluded[key]}; it cannot be part of the sketch"
                for lemma in lemmas
                if (key := statement_key(lemma.statement)) in (excluded or {})
                # Under a new definition, the same text is a new proposition.
                and set(lemma.definitions) <= set(definition_blocks(earlier))
            ]
            if not problems:
                candidate = Sketch(lemmas, main_proof)
                result = self._check(
                    "sketch",
                    self._standalone(form, self._sketch_body(form, candidate)),
                    repair=repair,
                )
                if result.ok:
                    logger.info(
                        f"sketch compiles with {len(lemmas)} "
                        f"lemma(s): {names}"
                    )
                    issues = self._judge_sketch(form, candidate, repair)
                    compiled = candidate, repair, issues
                    if issues and repair < self.config.max_repairs:
                        problems.append(
                            "The sketch compiles, but a reviewer rejected "
                            "its decomposition:\n" + issues
                        )
                        logger.info(f"sketch repair {repair}: judged REVISE")
                        self._decision(
                            "sketch_rejected", repair=repair, problems=problems
                        )
                        feedback = (
                            replan_feedback
                            + prompts.SKETCH_REPAIR_TEMPLATE.format(
                                lemmas=lemma_block,
                                main_proof=main_proof,
                                errors="\n".join(problems),
                            )
                        )
                        continue
                    self.sketch_rejected = issues
                    return self._accept_sketch(candidate, repair)
                problems.append(result.error_report())
            logger.info(f"sketch repair {repair}: {problems[0][:200]}")
            self._decision("sketch_rejected", repair=repair, problems=problems)
            feedback = replan_feedback + prompts.SKETCH_REPAIR_TEMPLATE.format(
                lemmas=lemma_block,
                main_proof=main_proof,
                errors="\n".join(problems),
            )
        if compiled is None:
            raise RuntimeError(
                "sketch did not compile within the repair budget"
            )
        candidate, repair, self.sketch_rejected = compiled
        logger.info(f"accepting the sketch compiled in repair {repair}")
        return self._accept_sketch(candidate, repair)

    def _revised_plan(self, form: Formalization, sketch: Sketch) -> Sketch:
        """`sketch`, or its revision on a reviewer's unanswered request."""
        if not self._revision_requested():
            return sketch
        revised = self.sketch(
            form,
            prompts.PLAN_REVISION_TEMPLATE.format(
                lemmas=stub_listing(sketch.lemmas),
                main_proof=sketch.main_proof,
                comments=self.config.sketch_feedback,
            ),
            earlier=sketch.lemmas,
        )
        self._decision("plan_revised", revision=self.config.plan_revision)
        return revised

    def _revision_requested(self) -> bool:
        """Whether a reviewer's request awaits its revision."""
        return bool(self.config.sketch_feedback) and not any(
            step.get("event") == "plan_revised"
            and step.get("revision") == self.config.plan_revision
            for step in self.trace.document["steps"]
        )

    def _excluded_statements(self, sketch: Sketch) -> dict[str, str]:
        """Statements a revision of `sketch` may not state again, with the
        reason: those refuted in this run (learned clauses outlive the
        sketch that taught them, cdcl.py), and those not proved whose
        attempt budget is spent."""
        excluded = {
            statement_key(statement): f"was refuted as `{name}`"
            for statement, name in self.clauses.refuted_statements().items()
        }
        excluded |= {
            statement_key(lemma.statement): f"was refuted as `{lemma.name}`"
            for lemma in sketch.lemmas
            if lemma.refutation
        }
        proved = {
            statement_key(lemma.statement)
            for lemma in sketch.lemmas
            if lemma.proved
        }
        for key, attempts in self.state.attempts.items():
            reviewed_at = self.state.reviewed_at.get(key, 0)
            allowed = self._attempts_allowed(reviewed_at)
            if key not in proved and attempts >= allowed:
                excluded.setdefault(
                    key,
                    f"failed {attempts} attempts, the run's budget for one "
                    f"statement",
                )
        return excluded

    def _accept_sketch(self, sketch: Sketch, repair: int) -> Sketch:
        for lemma in sketch.lemmas:
            self.state.load_attempts(lemma)
        self._decision(
            "sketch_accepted",
            repair=repair,
            lemmas=[lemma.statement for lemma in sketch.lemmas],
            definitions=definition_blocks(sketch.lemmas),
            main_proof=sketch.main_proof,
        )
        return sketch

    def _judge_sketch(
        self, form: Formalization, sketch: Sketch, repair: int
    ) -> str:
        """A second model's blocking objections to a compiled sketch (a lemma
        equivalent to or stronger than the theorem, an open lemma, a wrong
        statement, a missed case), and the lemmas it suspects of being false
        that a test refutes; empty if it accepts the sketch or judging is
        off. Advisory notes are recorded but never block. From the second
        round on, the judge sees its previous objections."""
        found = self._counterexamples(form, sketch)
        if not self.config.sketch_judge:
            return found
        previous = (
            prompts.JUDGE_PREVIOUS_TEMPLATE.format(issues=self._judged)
            if getattr(self, "_judged", "")
            else ""
        )
        reply = self._orchestrator(
            prompts.SKETCH_JUDGE_TEMPLATE.format(
                signature=form.signature,
                informal_proof=self.proof_for("judge"),
                lemmas=stub_listing(sketch.lemmas),
                main_proof=sketch.main_proof,
                previous=previous,
            ),
            phase=f"judge_sketch{repair}",
        )
        verdict = extract_tag(reply, "verdict").upper()
        issues = extract_tag(reply, "issues")
        names = set(extract_tag(reply, "suspect").split())
        suspects = [lemma for lemma in sketch.lemmas if lemma.name in names]
        self._decision(
            "sketch_judged",
            repair=repair,
            verdict=verdict,
            issues=issues,
            notes=extract_tag(reply, "notes"),
            suspects=[lemma.name for lemma in suspects],
        )
        logger.info(f"sketch judge {repair}: verdict {verdict}")
        refuted = self._test_suspects(form, sketch, suspects, issues)
        blocking = "" if verdict.startswith("ACCEPT") else (
            issues or "verdict: " + (verdict or "none given")
        )
        self._judged = blocking
        return "\n\n".join(filter(None, [found, refuted, blocking]))

    def _test_suspects(
        self, form: Formalization, sketch: Sketch, suspects: list[Lemma], reasons: str
    ) -> str:
        """The suspected lemmas that a counterexample search or a Lean proof
        of their negation refutes, with the evidence; empty if none is."""
        if not suspects:
            return ""
        found = self._counterexamples(form, sketch, suspects)
        reply = self._orchestrator(
            prompts.REFUTE_SUSPECTS_TEMPLATE.format(
                definitions=form.definitions,
                lemmas=stub_listing(sketch.lemmas),
                suspects=", ".join(f"`{lemma.name}`" for lemma in suspects),
                reasons=reasons or "(none given)",
            ),
            phase="refute_suspects",
        )
        proofs = extract_named(reply, "refutation")
        helpers = extract_named(reply, "helpers")
        refuted = []
        for lemma in suspects:
            proof = extract_tactics(
                f"<p>{proofs.get(lemma.name, '')}</p>", "p"
            )
            if not proof:
                continue
            text, problems = self._refutation(
                form,
                f"suspect_{lemma.name}",
                "\n\n".join([*lemma.definitions, lemma.statement]),
                lemma.name,
                drop_imports(helpers.get(lemma.name, "")),
                proof,
            )
            if not problems:
                refuted.append(
                    f"lemma `{lemma.name}` is false: Lean accepts this proof "
                    f"of its negation:\n{text}"
                )
        self._decision(
            "suspects_tested",
            suspects=[lemma.name for lemma in suspects],
            counterexamples=found,
            refuted=len(refuted),
        )
        logger.info(
            f"suspected lemmas {[lemma.name for lemma in suspects]}: "
            f"{len(refuted)} refuted, counterexamples: {bool(found)}"
        )
        return "\n\n".join(filter(None, [found, *refuted]))

    def _counterexamples(
        self, form: Formalization, sketch: Sketch, lemmas: list[Lemma] | None = None
    ) -> str:
        """Lemmas that Lean's `plausible` falsifies on random small instances,
        with the instance it found: `lemmas`, or every lemma of the sketch
        when the check is on. A lemma whose types `plausible` cannot sample
        yields no evidence either way."""
        if lemmas is None:
            if not self.config.lemma_counterexamples:
                return ""
            lemmas = sketch.lemmas
        shown: set[str] = set()
        definitions = [
            block for lemma in sketch.lemmas for block in new_definitions(lemma, shown)
        ]

        def test(lemma: Lemma) -> str:
            body = "\n\n".join([*definitions, f"{lemma.statement} := by\n  plausible"])
            result = self._check(
                f"counterexample_{lemma.name}",
                self._standalone(form, body),
                timeout=COUNTEREXAMPLE_SECONDS,
                lemma=lemma.name,
            )
            output = result.output
            if COUNTEREXAMPLE not in output:
                return ""
            instance = output.split(COUNTEREXAMPLE, 1)[1].split("-----", 1)[0]
            return f"lemma `{lemma.name}` is false: {COUNTEREXAMPLE}{instance.rstrip()}"

        with ThreadPoolExecutor(self.config.lean_parallel) as pool:
            found = [text for text in pool.map(test, lemmas) if text]
        if found:
            self._decision(
                "counterexamples", lemmas=len(found), found="\n\n".join(found)
            )
            logger.info(f"plausible falsified {len(found)} lemma(s)")
        return "\n\n".join(found)

    def _sketch_body(
        self, form: Formalization, sketch: Sketch, use_proofs: bool = False
    ) -> str:
        parts = []
        lemmas = (
            dependency_order(sketch.lemmas) if use_proofs else sketch.lemmas
        )
        shown: set[str] = set()
        for lemma in lemmas:
            parts += new_definitions(lemma, shown)
            if use_proofs and lemma.proved:
                if lemma.helpers:
                    parts.append(lemma.helpers)
                parts.append(f"{lemma.statement} := by\n{indent(lemma.proof)}")
            else:
                parts.append(f"{lemma.statement} := by sorry")
        parts.append(
            f"theorem {MAIN_NAME}{form.signature}:= by\n"
            f"{indent(sketch.main_proof)}"
        )
        return "\n\n".join(parts)

    def replan(
        self,
        form: Formalization,
        sketch: Sketch,
        resumed: bool = False,
        assembly_failure: str = "",
    ) -> Sketch:
        """Revise `sketch` after failed lemmas or an `assembly_failure` that
        no lemma caused. `resumed` marks the redo of a replan interrupted
        before its new sketch was accepted."""
        proved = [lemma for lemma in sketch.lemmas if lemma.proved]
        failed = [lemma for lemma in sketch.lemmas if not lemma.proved]
        refuted = [lemma.name for lemma in failed if lemma.refutation]
        excluded = self._excluded_statements(sketch)
        spent = [
            lemma
            for lemma in failed
            if not lemma.refutation
            and statement_key(lemma.statement) in excluded
        ]
        feedback = prompts.REPLAN_FEEDBACK_TEMPLATE.format(
            lemmas=stub_listing(sketch.lemmas),
            main_proof=sketch.main_proof,
            proved="\n\n".join(lemma.statement for lemma in proved) or "(none)",
            failed="\n\n".join(
                failed_lemma_text(lemma)
                + (
                    f"\nBUDGET SPENT: {lemma.attempts} failed attempts."
                    if lemma in spent
                    else ""
                )
                for lemma in failed
            ),
            clauses=self._clauses_text(failed, sketch.lemmas),
        )
        if assembly_failure:
            feedback += prompts.REPLAN_ASSEMBLY.format(
                failure=assembly_failure
            )
        if self.config.sketch_feedback and not self.config.sketch_approval:
            feedback += prompts.PLAN_FINDINGS_TEMPLATE.format(
                findings=self.config.sketch_feedback
            )
        self._decision(
            "replan_resumed" if resumed else "replan",
            proved=[lemma.name for lemma in proved],
            failed=[lemma.name for lemma in failed],
            refuted=sorted(refuted),
            spent=[lemma.name for lemma in spent],
        )
        if self.config.mode == "prove" and not resumed:
            self.row["informal_proof"] = self.redraft(form, feedback)
        new_sketch = self.sketch(form, feedback, excluded, sketch.lemmas)
        for lemma, before in reinstated(sketch.lemmas, new_sketch.lemmas):
            # A dropped lemma that a kept proof uses returns with
            # `aiprover_escalation_after` more attempts, as after a retry.
            self.state.load_attempts(lemma)
            self.state.record_review(lemma)
            logger.info(f"{lemma.name}: reinstated; {before} is proved from it")
            self._decision(
                "lemma_reinstated",
                lemma=lemma.name,
                statement=lemma.statement,
                definitions=list(lemma.definitions),
                before=before,
                attempts=lemma.attempts,
            )
        proofs = {normalize(lemma.statement): lemma for lemma in proved}
        kept = unchanged_names(
            statements(sketch.lemmas), statements(new_sketch.lemmas)
        )
        old_names = {lemma.name for lemma in sketch.lemmas}
        changed = definitions_changed(sketch.lemmas, new_sketch.lemmas)
        reused = []
        for lemma in new_sketch.lemmas:
            previous = proofs.get(normalize(lemma.statement))
            if not previous:
                continue
            missing = missing_lemmas(previous, old_names, kept)
            reused.append((lemma, previous, changed or bool(missing)))
        self.reuse_proofs(form, new_sketch, reused)
        if (self.result_dir / "blueprint").exists():
            self.blueprint(form, new_sketch)
        return new_sketch

    def reuse_proofs(
        self,
        form: Formalization,
        sketch: Sketch,
        proofs: list[tuple[Lemma, Lemma, bool]],
    ) -> None:
        """`_reuse_proof` of each of `proofs`, whose checks are independent.
        A timeout is recorded once, however many checks reach it."""
        with ThreadPoolExecutor(self.config.lean_parallel) as pool:
            try:
                list(
                    pool.map(
                        lambda proof: self._reuse_proof(form, sketch, *proof),
                        proofs,
                    )
                )
            except AgentCallError:
                pool.shutdown(wait=False, cancel_futures=True)
                self._decision("lean_timed_out", check="recheck")
                raise

    def _reuse_proof(
        self,
        form: Formalization,
        sketch: Sketch,
        lemma: Lemma,
        previous: Lemma,
        recheck: bool,
    ) -> None:
        """Give `lemma` the proof of `previous`, which must first compile on
        `sketch` if `recheck`, so that a small change does not discard the
        proofs that use it. A timeout stops the run: its resume repeats the
        check with twice the limit, since the machine, not the proof, may
        be the cause."""
        if recheck:
            result, problem = self._check_proof(
                form,
                stub_listing(
                    [other for other in sketch.lemmas if other is not lemma],
                    before=lemma,
                ),
                lemma,
                previous.helpers,
                previous.proof,
                f"{lemma.name}_recheck",
                timeout=self.state.lean_limit(self.config.lean_timeout),
            )
            if result.timed_out:
                raise AgentCallError(
                    f"Lean check of the kept proof of {lemma.name} timed out"
                )
            if problem:
                logger.info(
                    f"{lemma.name}: proof not reused; it fails on the new "
                    f"sketch"
                )
                self._decision("lemma_recheck_failed", lemma=lemma.name)
                return
        lemma.helpers, lemma.proof = previous.helpers, previous.proof
        lemma.proved = True
        self._decision(
            "lemma_reused",
            lemma=lemma.name,
            helpers=lemma.helpers,
            proof=lemma.proof,
        )
