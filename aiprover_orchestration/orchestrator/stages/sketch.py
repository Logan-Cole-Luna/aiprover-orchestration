"""Setting check, proof sketch, refutation and replanning.

Before planning, the captain tests the setting: facts it proves about it
(a degenerate structure, contradictory hypotheses, a small case) join the
definitions. The captain then decomposes the proof into lemmas (stated in
full, left as `sorry`) and a proof of `solution` from them; the sketch must
compile. After failed lemmas, the captain revises the sketch with the
solvers' failed code and errors; proved lemmas whose statements are
unchanged keep their proofs. A statement the captain judges false, the
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
from ..structures import (
    Formalization,
    Lemma,
    Sketch,
    carry_definitions,
    dependency_order,
    extract_tag,
    extract_tactics,
    failed_lemma_text,
    missing_lemmas,
    new_definitions,
    parse_lemmas,
    statement_key,
    statements,
    stub_listing,
    unchanged_names,
)

logger = logging.getLogger(__name__)

# Facts proved by the setting check, which join the definitions.
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

    def check_setting(self, form: Formalization) -> str:
        """Facts about the setting that the captain proves before planning:
        Lean theorems named `setting_...`, compiled against the definitions
        without `sorry`. Empty when the captain finds none."""
        feedback, facts = "", ""
        for repair in range(2):
            reply = self._captain(
                prompts.SETTING_CHECK_TEMPLATE.format(
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
            logger.info(f"setting check repair {repair}: {problems[0][:200]}")
            feedback = prompts.SETTING_CHECK_REPAIR_TEMPLATE.format(
                facts=facts, errors="\n".join(problems)[:6000]
            )
            facts = ""
        self._decision("setting_checked", facts=facts)
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
        for repair in range(self.config.max_repairs + 1):
            reply = self._captain(
                prompts.SKETCH_TEMPLATE.format(
                    definitions=form.definitions,
                    preamble=form.preamble,
                    signature=form.signature,
                    informal_proof=self.row["informal_proof"],
                    feedback=feedback,
                ),
                phase=f"sketch{repair}",
            )
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
            carry_definitions(lemmas, earlier)
            names = [lemma.name for lemma in lemmas]
            problems = [
                f"lemma `{name}` must be stated with `:= by sorry`"
                for kind, name, _ in split_declarations(lemma_block)
                if kind in ("theorem", "lemma") and name not in names
            ]
            problems += forbidden_problems(main_proof, "main proof")
            problems += forbidden_problems(
                lemma_block, "lemma statements", statements=True
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
                    compiled = candidate, repair
                    issues = self._judge_sketch(form, candidate, repair)
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
        candidate, repair = compiled
        logger.info(f"accepting the sketch compiled in repair {repair}")
        return self._accept_sketch(candidate, repair)

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
            main_proof=sketch.main_proof,
        )
        return sketch

    def _judge_sketch(
        self, form: Formalization, sketch: Sketch, repair: int
    ) -> str:
        """A second model's objections to a compiled sketch's decomposition
        (a lemma equivalent to or stronger than the theorem, an open or false
        lemma, a lemma that is not self-contained or bundles several steps);
        empty if it accepts the sketch or judging is off."""
        if not self.config.sketch_judge:
            return ""
        reply = self._captain(
            prompts.SKETCH_JUDGE_TEMPLATE.format(
                signature=form.signature,
                informal_proof=self.row["informal_proof"],
                lemmas=stub_listing(sketch.lemmas),
                main_proof=sketch.main_proof,
            ),
            phase=f"judge_sketch{repair}",
        )
        verdict = extract_tag(reply, "verdict").upper()
        issues = extract_tag(reply, "issues")
        self._decision(
            "sketch_judged", repair=repair, verdict=verdict, issues=issues
        )
        logger.info(f"sketch judge {repair}: verdict {verdict}")
        if verdict.startswith("ACCEPT"):
            return ""
        return issues or "verdict: " + (verdict or "none given")

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
        self, form: Formalization, sketch: Sketch, resumed: bool = False
    ) -> Sketch:
        """Revise `sketch` after failed lemmas. `resumed` marks the redo of a
        replan interrupted before its new sketch was accepted."""
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
        self._decision(
            "replan_resumed" if resumed else "replan",
            proved=[lemma.name for lemma in proved],
            failed=[lemma.name for lemma in failed],
            refuted=sorted(refuted),
            spent=[lemma.name for lemma in spent],
        )
        new_sketch = self.sketch(form, feedback, excluded, sketch.lemmas)
        proofs = {normalize(lemma.statement): lemma for lemma in proved}
        kept = unchanged_names(
            statements(sketch.lemmas), statements(new_sketch.lemmas)
        )
        old_names = {lemma.name for lemma in sketch.lemmas}
        for lemma in new_sketch.lemmas:
            previous = proofs.get(normalize(lemma.statement))
            if not previous:
                continue
            missing = missing_lemmas(previous, old_names, kept)
            if missing:
                logger.info(
                    f"{lemma.name}: proof not reused; it uses {missing}, "
                    f"which the new sketch drops"
                )
                continue
            lemma.helpers, lemma.proof = previous.helpers, previous.proof
            lemma.proved = True
            self._decision("lemma_reused", lemma=lemma.name)
        return new_sketch
