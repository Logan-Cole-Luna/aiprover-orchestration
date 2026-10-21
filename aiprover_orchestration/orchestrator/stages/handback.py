"""The captain during the AIProver phase: hand-back and conflict analysis.

A lemma the solvers keep failing on is handed back to the captain
(`_handback`), who proves, splits, restates or refutes it, or asks for a
retry. Conflict-driven learning (cdcl.py): when several leaves under one
decision are stuck, the captain analyses the conflict (`_conflict`). What it
learns is recorded as clauses that every later job receives, and when the
cause lies in the decision it backjumps: the subtree is retracted, its
running jobs are cancelled, and the decision is restated. Every proof,
statement and refutation the captain proposes is checked by Lean before it
is applied; one repair is allowed.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Splitting a lemma the prover fails on into subgoals: Hilbert
  (Varambally et al., ICLR 2026), https://github.com/Rose-STL-Lab/ml-hilbert
- [Similar] Refining the plan from failed lemmas: Goedel-Architect (Chung
  et al., ICML 2026; no code released), https://icml.cc/virtual/2026/82541
"""

import logging
import re

from ...agents.base import AgentCallError
from ...lean.checker import forbidden_problems
from ...lean.text import drop_imports, strip_leading_by
from .. import cdcl, knowledge, prompts
from ..extraction import helper_names, proved_helper_names
from ..structures import (
    Lemma,
    Sketch,
    extract_named,
    extract_tag,
    extract_tactics,
    failed_lemma_text,
    parse_lemmas,
    refers_to,
    stub_listing,
)

logger = logging.getLogger(__name__)


class HandbackStage:

    def _captain_repairs(self, prompt: str, phase: str, apply) -> list[str]:
        """Ask the captain, and once more with the problems of a rejected
        reply (HANDBACK_REPAIR_TEMPLATE) while the budget allows.
        `apply(reply)` applies a reply and returns its problems, empty when
        it was accepted. Returns the problems of the last reply, empty if
        one was accepted."""
        feedback, problems = "", ["no reply"]
        for repair in range(2):
            if repair and self.agents.claude_budget_left() < 1:
                break
            reply = self._captain(prompt + feedback, phase=phase)
            problems = apply(reply)
            if not problems:
                return []
            logger.info(f"{phase}: reply rejected: {problems[0][:200]}")
            feedback = prompts.HANDBACK_REPAIR_TEMPLATE.format(
                reply=reply, errors="\n".join(problems)[:6000]
            )
        return problems

    def _restated_lemma(
        self,
        phase,
        target: Lemma,
        restated: str,
        tag: str,
        label: str,
        without: frozenset = frozenset(),
    ) -> tuple[Lemma | None, list[str]]:
        """The lemma a captain's <tag> states in place of `target`, if the
        sketch compiles with it (and without the lemmas `without`); else
        None and the problems."""
        new = parse_lemmas(
            restated if ":=" in restated else restated + " := by sorry"
        )
        if len(new) != 1 or new[0].name != target.name:
            return None, [
                f"<{tag}> must state exactly `theorem {target.name} ...`"
            ]
        with phase.lock:
            candidate = Sketch(
                [
                    new[0] if other is target else other
                    for other in phase.sketch.lemmas
                    if other.name not in without
                ],
                phase.sketch.main_proof,
            )
        result = self._check(
            label,
            self._standalone(
                phase.form, self._sketch_body(phase.form, candidate)
            ),
            lemma=target.name,
        )
        if not result.ok:
            return None, [result.error_report()]
        return new[0], []

    def _handback(self, phase, lemma: Lemma) -> None:
        """Hand `lemma`, which the solvers keep failing on, back to the
        captain. New lemmas of a split go to solvers at once."""
        form, sketch = phase.form, phase.sketch
        with phase.lock:
            if not phase.present(lemma):
                return  # retracted by a backjump
            lemma.handed_back = True
            listing = marked_listing(sketch.lemmas, lemma, "this lemma")
            stubs = stub_listing(sketch.lemmas[: sketch.lemmas.index(lemma)])
            names = {other.name for other in sketch.lemmas}
            other_helpers = proved_helper_names(sketch.lemmas, lemma)
        retries = self._retries_spent(phase, lemma)
        prompt = prompts.HANDBACK_TEMPLATE.format(
            attempts=lemma.attempts,
            lemma_name=lemma.name,
            definitions=form.definitions,
            preamble=form.preamble,
            lemmas=listing,
            main_proof=sketch.main_proof,
            informal_proof=self.row["informal_proof"],
            failed=failed_lemma_text(lemma),
            knowledge=self._for_captain(lemma.knowledge) or "(none)",
            clauses=self._clauses_text([lemma], list(sketch.lemmas)),
            clause_output=self._clause_output(),
            retry_note=prompts.NO_RETRY.format(retries=retries)
            if retries
            else "",
        )

        def record(action: str, diagnosis: str, **fields) -> None:
            self._decision(
                "lemma_handback",
                lemma=lemma.name,
                action=action,
                diagnosis=diagnosis,
                **fields,
            )

        def used_by_proofs() -> bool:
            """Whether a proved lemma uses `lemma` (call with `phase.lock`
            held): other lemmas are proved while the captain answers."""
            return any(
                refers_to(other.helpers + "\n" + other.proof, lemma.name)
                for other in sketch.lemmas
                if other.proved
            )

        def restate(diagnosis: str, restated: str, reply: str) -> list[str]:
            used = [f"proved lemmas use `{lemma.name}`; it cannot be restated"]
            with phase.lock:
                if used_by_proofs():
                    return used
            new, problems = self._restated_lemma(
                phase, lemma, restated, "restate", f"{lemma.name}_restated"
            )
            if new is None:
                return problems
            old = lemma.statement
            with phase.lock:
                if used_by_proofs():
                    return used
                lemma.statement = new.statement
                lemma.handed_back = False
                phase.retries.pop(lemma.name, None)
                self.state.load_attempts(lemma)
                if "<knowledge>" not in reply:
                    lemma.knowledge = ""
            logger.info(f"{lemma.name} restated by the captain")
            record(
                "restate",
                diagnosis,
                statement=lemma.statement,
                previous_statement=old,
                knowledge=lemma.knowledge,
            )
            return []

        def refute(diagnosis: str, helpers: str, refutation: str) -> list[str]:
            text, rejections = self._refutation(
                form,
                f"{lemma.name}_refuted",
                lemma.statement,
                lemma.name,
                helpers,
                refutation,
                lemma=lemma.name,
                worker="captain",
            )
            if rejections:
                return rejections
            with phase.lock:
                lemma.refutation = text
            logger.info(f"{lemma.name} refuted by the captain")
            self._learn(
                cdcl.LearnedClause(
                    "refuted",
                    lemma.statement,
                    "its negation compiled in Lean",
                    lemma.name,
                )
            )
            record("refute", diagnosis, refutation=text)
            return []

        def prove(
            diagnosis: str, helpers: str, proof: str, split_block: str
        ) -> list[str]:
            """The captain's proof, from new lemmas if it splits."""
            new_lemmas = parse_lemmas(split_block) if split_block else []
            problems = [
                f"new lemma `{other.name}` reuses an existing name"
                for other in new_lemmas
                if other.name in names or other.name == lemma.name
            ]
            problems += [
                f"helper `{name}` reuses the name of a lemma or of "
                f"another lemma's helper"
                for name in sorted(
                    helper_names(helpers)
                    & (names | other_helpers | helper_names(split_block))
                )
            ]
            problems += forbidden_problems(helpers + "\n" + proof)
            problems += forbidden_problems(
                split_block, "new lemmas", statements=True
            )
            if problems:
                return problems
            result, problem = self._check_proof(
                form,
                "\n\n".join(filter(None, [stubs, stub_listing(new_lemmas)])),
                lemma,
                helpers,
                proof,
                f"{lemma.name}_captain",
                worker="captain",
            )
            if result.timed_out:
                # The machine, not the reply: the run stops and its resume
                # repeats this hand-back.
                raise AgentCallError(
                    f"Lean check of the hand-back of {lemma.name} timed out"
                )
            if problem:
                return [problem]
            with phase.lock:
                if not phase.present(lemma):
                    return []  # retracted meanwhile
                for other in new_lemmas:
                    self.state.load_attempts(other)
                cdcl.insert_split(sketch.lemmas, lemma, new_lemmas)
                lemma.helpers, lemma.proof = helpers, proof
                lemma.proved = True
            action = "split" if new_lemmas else "proof"
            new_names = [other.name for other in new_lemmas]
            logger.info(
                f"{lemma.name}: captain {action}"
                + (f" into {new_names}" if new_lemmas else "")
            )
            record(
                action,
                diagnosis,
                new_lemmas=[other.statement for other in new_lemmas],
            )
            self._record_proof(lemma, "captain")
            for other in new_lemmas:
                self._submit(phase, other)
            return []

        def apply(reply: str) -> list[str]:
            diagnosis = extract_tag(reply, "diagnosis")
            helpers = drop_imports(extract_tag(reply, "helpers"))
            if "<knowledge>" in reply:
                # Applied with whichever action follows: the next sessions
                # on this lemma, or its new statement, start from it.
                lemma.knowledge = extract_tag(reply, "knowledge")
            for fact in extract_clauses(reply):
                self._learn(cdcl.LearnedClause("fact", "", fact, lemma.name))
            if restated := extract_tag(reply, "restate"):
                return restate(diagnosis, restated, reply)
            if refutation := extract_tactics(reply, "refute"):
                return refute(diagnosis, helpers, refutation)
            if proof := extract_tactics(reply, "proof"):
                split_block = drop_imports(extract_tag(reply, "split"))
                return prove(diagnosis, helpers, proof, split_block)
            if "<retry" in reply and retries:
                return [f"retry is not available after {retries} retries"]
            if "<retry" in reply:
                logger.info(f"{lemma.name}: captain asks for a retry")
                # The captain reviews the lemma again after as many further
                # failures as led to this hand-back.
                with phase.lock:
                    lemma.handed_back = False
                    self.state.record_review(lemma)
                    phase.retries[lemma.name] += 1
                record(
                    "retry",
                    diagnosis,
                    attempts=lemma.attempts,
                    knowledge=lemma.knowledge,
                )
                return []
            return ["the reply names no action"]

        problems = self._captain_repairs(
            prompt, f"handback/{lemma.name}", apply
        )
        if problems:
            record("rejected", "", problems=problems[:5])

    def _conflict(
        self, phase, decision: Lemma, stuck_leaves: list[Lemma]
    ) -> None:
        """Conflict analysis at `decision`, whose subtree has the stuck
        leaves `stuck_leaves`. The captain names the cause and records
        learned clauses. If the cause lies in the decision, it backjumps:
        the subtree is retracted, and the decision is restated (checked by
        Lean against the sketch without the subtree) and handed to
        solvers."""
        with phase.lock:
            listing = marked_listing(
                phase.sketch.lemmas, decision, "the decision"
            )
            names = [other.name for other in stuck_leaves]
        stuck = "\n\n".join(
            failed_lemma_text(other)
            + "\n<notes>\n"
            + self._for_captain(other.knowledge)[: knowledge.MAX_CHARS // 2]
            + "\n</notes>"
            for other in stuck_leaves
        )
        prompt = prompts.CONFLICT_TEMPLATE.format(
            definitions=phase.form.definitions,
            preamble=phase.form.preamble,
            informal_proof=self.row["informal_proof"],
            main_proof=phase.sketch.main_proof,
            lemmas=listing,
            decision_name=decision.name,
            decision_statement=decision.statement,
            decision_proof=(
                f"{decision.helpers}\n{decision.proof}".strip() or "(none)"
            ),
            stuck=stuck,
            clauses=self._clauses_text(
                [decision, *stuck_leaves], list(phase.sketch.lemmas)
            ),
            clause_output=self._clause_output(),
            backjump_action=(
                prompts.BACKJUMP_ACTION.format(decision_name=decision.name)
                if self.config.cdcl_backjump
                else prompts.NO_BACKJUMP
            ),
        )
        logger.info(
            f"conflict at {decision.name}: {len(names)} stuck lemma(s) "
            f"{names}; analysing"
        )
        conflict = {
            "lemma": decision.name,
            "statement": decision.statement,
            "stuck": names,
        }
        diagnosis = ""

        def apply(reply: str) -> list[str]:
            nonlocal diagnosis
            diagnosis = extract_tag(reply, "diagnosis")
            problems = self._leaf_proofs(phase, reply, stuck_leaves)
            if problems:
                return problems
            facts = extract_clauses(reply)
            restated = extract_tag(reply, "backjump")
            if restated and not self.config.cdcl_backjump:
                return ["backjumping is off in this run; choose <local/>"]
            if not restated and "<local" in reply:
                self._decision(
                    "conflict", **conflict, action="local", diagnosis=diagnosis
                )
                # Without a shared cause the clauses are routes and pitfalls
                # for the leaves, not failed decisions.
                for fact in facts:
                    self._learn(
                        cdcl.LearnedClause("fact", "", fact, decision.name)
                    )
                logger.info(f"conflict at {decision.name}: local causes")
                return []
            if not restated:
                return ["the reply names no action"]
            with phase.lock:
                subtree = cdcl.subtree(decision.name, phase.sketch.lemmas)
                below = frozenset(other.name for other in subtree)
            new, problems = self._restated_lemma(
                phase,
                decision,
                restated,
                "backjump",
                f"{decision.name}_backjump",
                without=below,
            )
            if new is None:
                return problems
            previous = decision.statement
            with phase.lock:
                removed = self._retract(phase, decision)
                decision.statement = new.statement
                decision.knowledge = ""
                self.state.load_attempts(decision)
            retracted = [other.name for other in removed]
            self._decision(
                "conflict", **conflict, action="backjump", diagnosis=diagnosis
            )
            self._decision(
                "backjump",
                lemma=decision.name,
                statement=decision.statement,
                previous_statement=previous,
                retracted=retracted,
                diagnosis=diagnosis,
            )
            self._learn(
                cdcl.LearnedClause(
                    "conflict_cause", previous, diagnosis, decision.name
                )
            )
            for fact in facts:
                self._learn(
                    cdcl.LearnedClause(
                        "failed_route", previous, fact, decision.name
                    )
                )
            logger.info(
                f"backjump to {decision.name}: restated, "
                f"{len(retracted)} lemma(s) retracted"
            )
            self._submit(phase, decision)
            return []

        problems = self._captain_repairs(
            prompt, f"conflict/{decision.name}", apply
        )
        if problems:
            self._decision(
                "conflict",
                **conflict,
                action="rejected",
                diagnosis=diagnosis,
                problems=problems[:5],
            )


    def _leaf_proofs(
        self, phase, reply: str, stuck_leaves: list[Lemma]
    ) -> list[str]:
        """Prove the stuck leaves for which a conflict analysis gave a proof
        (<leaf_proof name=...>), each checked by Lean against the lemmas
        before it, as unit propagation closes a forced literal at once.
        Leaves proved before a rejected one stay proved, so a repair round
        repeats only what failed."""
        proofs = extract_named(reply, "leaf_proof")
        all_helpers = extract_named(reply, "leaf_helpers")
        by_name = {leaf.name: leaf for leaf in stuck_leaves}
        problems = [
            f"`{name}` is not a stuck lemma of this conflict"
            for name in proofs
            if name not in by_name
        ]
        for name, proof in proofs.items():
            leaf = by_name.get(name)
            if leaf is None or leaf.proved:
                continue
            helpers = drop_imports(all_helpers.get(name, ""))
            proof = strip_leading_by(proof)
            with phase.lock:
                lemmas = phase.sketch.lemmas
                stubs = stub_listing(lemmas[: lemmas.index(leaf)])
                taken = {other.name for other in lemmas} | proved_helper_names(
                    lemmas, leaf
                )
            found = [
                f"helper `{helper}` of `{name}` reuses the name of a lemma "
                f"or of another lemma's helper"
                for helper in sorted(helper_names(helpers) & taken)
            ] + forbidden_problems(helpers + "\n" + proof)
            if not found:
                result, problem = self._check_proof(
                    phase.form,
                    stubs,
                    leaf,
                    helpers,
                    proof,
                    f"{name}_conflict",
                    worker="captain",
                )
                if result.timed_out:
                    raise AgentCallError(
                        f"Lean check of the conflict proof of {name} timed out"
                    )
                found = [f"`{name}`: {problem}"] if problem else []
            if found:
                problems += found
                continue
            with phase.lock:
                if not phase.present(leaf) or leaf.proved:
                    continue
                leaf.helpers, leaf.proof = helpers, proof
                leaf.proved = True
            logger.info(f"{name}: proved in conflict analysis")
            self._record_proof(leaf, "captain")
        return problems


def marked_listing(lemmas: list[Lemma], target: Lemma, mark: str) -> str:
    """The lemmas as stubs, the proved ones and `target` marked."""
    return stub_listing(
        lemmas,
        lambda other: ("  -- proved" if other.proved else "")
        + (f"  -- {mark}" if other is target else ""),
    )


def extract_clauses(reply: str) -> list[str]:
    """The learned clauses of a captain's reply, one per <clause> tag."""
    return [
        " ".join(fact.split())
        for fact in re.findall(r"<clause>(.*?)</clause>", reply, re.S)
        if fact.strip()
    ]
