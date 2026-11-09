"""Theorem proving with open steps (`Config.open_subgoals`, `Config.approaches`,
`Config.orchestrator_first`).

A proof attempt may contain steps that no one can yet prove. The orchestrator
drafts several approaches, each with its gaps named, and a judge orders them
by evidence: a gap that is false, or as strong as the theorem, rules its
approach out. The sketch names each open step `gap_...`. Routine lemmas are
proved first, by the orchestrator where a short proof suffices and by AIProver
otherwise; a gap receives `GAP_ATTEMPTS` jobs once every routine lemma is
proved, after which its escalation may split it into smaller steps. A sketch
the judge still rejects after its repairs passes to the next approach. When
only gaps remain, the run records a checkpoint, a file in which Lean has
checked that the theorem follows from the gap lemmas, the only statements
left as `sorry`, and continues on the gaps through the replan. A gap is
never assumed: it stays a proof target.

Related published work:
- [Similar] Several ranked decompositions, each checked to imply the
  theorem: arXiv 2603.19329 (COLM 2026).
- [Similar] Recursive decomposition of failed subgoals: HILBERT (ICLR 2026),
  https://openreview.net/pdf?id=ljAHonPrs1
- [Similar] Strategy portfolios with counterexample search before proof:
  Vampire and E schedules; Isabelle's Nitpick before Sledgehammer.
"""

import logging

from ...lean import checker
from ...lean.text import drop_imports
from .. import prompts
from ..config import MAIN_NAME
from ..structures import (
    Formalization,
    Sketch,
    extract_named,
    extract_tactics,
    extract_tag,
    stub_listing,
)

logger = logging.getLogger(__name__)

GAP_PREFIX = "gap_"
# AIProver jobs a gap receives before its escalation, which may split it.
GAP_ATTEMPTS = 2


def is_gap(lemma) -> bool:
    return lemma.name.startswith(GAP_PREFIX)


class GapStage:

    def draft_approaches(self, form: Formalization) -> str:
        """`config.approaches` drafts, each with its gaps, kept as
        approach_<n>.md in the order the judge ranks them; returns the
        first. One draft is the plain draft."""
        if self.config.approaches < 2:
            return self.draft_proof(form)
        ranked = sorted(self.result_dir.glob("approach_*.md"))
        if not ranked:
            reply = self._orchestrator(
                prompts.APPROACHES_TEMPLATE.format(
                    count=self.config.approaches,
                    informal_statement=self.row["informal_statement"],
                    definitions=self._own_definitions(form),
                    library=self.library or "(none)",
                    statement=form.statement,
                ),
                phase="draft",
            )
            drafts = list(extract_named(reply, "approach").values())
            if not drafts:
                raise ValueError("the draft has no <approach>")
            order = self._rank_approaches(form, drafts)
            for rank, index in enumerate(order, 1):
                path = self.result_dir / f"approach_{rank}.md"
                path.write_text(drafts[index] + "\n")
            ranked = sorted(self.result_dir.glob("approach_*.md"))
        (self.result_dir / "draft.md").write_text(ranked[0].read_text())
        return ranked[0].read_text()

    def _rank_approaches(self, form: Formalization, drafts: list[str]) -> list[int]:
        """Indices of `drafts`, most promising first, as the judge orders
        them; an unreadable order keeps the drafter's."""
        listing = "\n\n".join(
            f'<approach name="{k + 1}">\n{draft}\n</approach>'
            for k, draft in enumerate(drafts)
        )
        reply = self._orchestrator(
            prompts.RANK_APPROACHES_TEMPLATE.format(
                statement=form.statement, approaches=listing
            ),
            phase="judge_approaches",
        )
        named = [
            int(word) - 1
            for word in extract_tag(reply, "order").replace(",", " ").split()
            if word.isdigit() and 0 < int(word) <= len(drafts)
        ]
        order = list(dict.fromkeys(named)) + [
            k for k in range(len(drafts)) if k not in named
        ]
        self._decision(
            "approaches_ranked",
            order=[k + 1 for k in order],
            reasons=extract_tag(reply, "reasons"),
        )
        logger.info(f"approaches ranked: {[k + 1 for k in order]}")
        return order

    def next_approach(self) -> bool:
        """Make the next ranked approach the draft; False if none is left."""
        switched = sum(
            step.get("event") == "approach_switched"
            for step in self.trace.document["steps"]
        )
        drafts = sorted(self.result_dir.glob("approach_*.md"))
        if switched + 1 >= len(drafts):
            return False
        draft = drafts[switched + 1].read_text()
        self.row["informal_proof"] = draft
        (self.result_dir / "draft.md").write_text(draft)
        self._decision("approach_switched", approach=switched + 2)
        logger.info(f"switching to approach {switched + 2}")
        return True

    def restore_first_approach(self, sketch: Sketch, draft: str) -> Sketch:
        """Return to the best-ranked approach's last sketch once every
        approach has been rejected: a later approach ranked lower."""
        self.row["informal_proof"] = draft
        (self.result_dir / "draft.md").write_text(draft)
        self._decision("approach_restored", approach=1)
        logger.info("every approach rejected; returning to approach 1")
        return self._accept_sketch(sketch, self.config.max_repairs)

    def orchestrator_proofs(self, form: Formalization, sketch: Sketch) -> None:
        """One orchestrator call proves the routine lemmas it can prove quickly;
        each proof must pass the same Lean check as a prover's."""
        pending = [
            lemma
            for lemma in sketch.lemmas
            if not lemma.proved and not is_gap(lemma)
        ]
        if not pending:
            return
        reply = self._orchestrator(
            prompts.ORCHESTRATOR_PROOFS_TEMPLATE.format(
                definitions=self._own_definitions(form),
                preamble=form.preamble,
                lemmas=stub_listing(sketch.lemmas),
                pending=", ".join(f"`{lemma.name}`" for lemma in pending),
            ),
            phase="orchestrator_proofs",
        )
        proofs = extract_named(reply, "proof")
        helpers = extract_named(reply, "helpers")
        for lemma in pending:
            proof = extract_tactics(f"<proof>{proofs.get(lemma.name, '')}</proof>", "proof")
            if not proof:
                continue
            own_helpers = drop_imports(helpers.get(lemma.name, ""))
            problems = checker.forbidden_problems(own_helpers + "\n" + proof)
            if not problems:
                k = sketch.lemmas.index(lemma)
                _, problem = self._check_proof(
                    form,
                    stub_listing(sketch.lemmas[:k], before=lemma),
                    lemma,
                    own_helpers,
                    proof,
                    f"{lemma.name}_orchestrator",
                )
                problems = [problem] if problem else []
            if problems:
                logger.info(f"{lemma.name}: orchestrator's proof rejected")
                continue
            lemma.helpers, lemma.proof, lemma.proved = own_helpers, proof, True
            logger.info(f"lemma {lemma.name} proved by the orchestrator")
            self._record_proof(lemma, "orchestrator")

    def reduction(self, form: Formalization, sketch: Sketch) -> dict:
        """The assembled file with only the gap lemmas left as `sorry`, and
        whether Lean accepts it with every other lemma free of `sorry`."""
        standalone, checks = self.assemble(form, sketch)
        routine = [lemma.name for lemma in sketch.lemmas if not is_gap(lemma)]
        result = self._assembly_check(
            "reduction",
            standalone + "".join(f"\n#print axioms {name}" for name in routine) + "\n",
        )
        clean = all(
            checker.nonstandard_axioms(result, name) == [] for name in routine
        )
        gaps = [lemma.statement for lemma in sketch.lemmas if is_gap(lemma)]
        verified = result.ok and clean and checks["statement_matches"]
        (self.result_dir / f"{self.slug}_reduction.lean").write_text(standalone)
        self._decision("reduction", verified=verified, gaps=gaps)
        logger.info(
            f"reduction to {len(gaps)} gap(s): "
            + ("verified" if verified else "not verified")
        )
        return {"verified": verified, "gaps": gaps, "main": MAIN_NAME}
