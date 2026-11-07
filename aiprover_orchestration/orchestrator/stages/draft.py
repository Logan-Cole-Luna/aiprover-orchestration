"""Theorem-proving mode (`Config.mode == "prove"`): the run finds the proof
instead of translating one.

A row's `formal_statement` (Lean definitions, then one
`theorem <name> ... := by sorry`) is kept exactly as given: compiled, never
audited or revised, and renamed to the run's slug. A row without one has its
informal statement formalized and audited as in autoformalization. Any
informal proof of the row is withheld. The captain then drafts the informal
proof that the sketch decomposes; the draft is kept as `draft.md` in the
result directory, so that a resumed run plans from the same draft.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Informal drafts guiding formal proofs: Draft, Sketch, and Prove
  (Jiang et al., ICLR 2023), https://arxiv.org/abs/2210.12283
"""

import logging
import re

from ...lean.text import drop_imports
from .. import prompts
from ..config import FILE_HEADER
from ..structures import Formalization, extract_tag, parse_statement

logger = logging.getLogger(__name__)

FIXED_VERDICT = "FAITHFUL (fixed formal statement)"
# How the judge and the reviewer read the proof of a run in this mode.
DRAFT_NOTES = {
    "judge": "No source proof exists: the proof below is the orchestrator's "
    "own draft. Judge whether each lemma holds and is simpler than the "
    "theorem, not whether it follows the draft.",
    "review": "No source proof exists: the proof below is the orchestrator's "
    "own draft, which the formal proof need not follow. Lean has checked the "
    "proof, so judge the definitions and the statement against the source "
    "statement; a departure from the draft is no reason for UNFAITHFUL.",
}
WITHHELD_PROOF = "(The proof is to be found.)"
NEGATED_STATEMENT = (
    "The run proves the negation of the statement below, which the "
    "orchestrator judged false.\n\n{}"
)
_THEOREM = re.compile(r"^(?:@\[[^\]]*\]\s*)?theorem\s", re.M)
_OPENING, _CLOSING = "([{⦃⟨", ")]}⦄⟩"


def negation(signature: str) -> str:
    """The signature of the statement's negation: its binders, which may
    precede the type's colon, move under a universal quantifier."""
    depth = 0
    for index, char in enumerate(signature):
        depth += (char in _OPENING) - (char in _CLOSING)
        if char == ":" and depth == 0:
            binders = signature[:index].strip()
            claim = signature[index + 1 :].strip()
            break
    else:
        raise ValueError(f"no type in the signature {signature!r}")
    body = f"∀ {binders}, {claim}" if binders else claim
    return f" : ¬ ({body}) "


class DraftStage:
    def withhold_proof(self) -> None:
        """Hide any informal proof of the row from every role, and state the
        formal statement as the informal one where the row has none."""
        self.row["informal_proof"] = WITHHELD_PROOF
        if not self.row.get("informal_statement"):
            self.row["informal_statement"] = self.row["formal_statement"]

    def fixed_formalization(self) -> Formalization | None:
        """The row's formal statement as the run's formalization, or None if
        it does not compile."""
        text = drop_imports(self.row["formal_statement"]).strip()
        starts = list(_THEOREM.finditer(text))
        if not starts:
            raise ValueError("a formal statement needs a `theorem`")
        definitions = text[: starts[-1].start()].strip()
        preamble, name, signature = parse_statement(text[starts[-1].start() :])
        if self.library:
            definitions = f"{self.library}\n\n{definitions}".strip()
        form = Formalization(
            definitions,
            preamble,
            self.slug,
            signature,
            notes=f"Fixed formal statement of the row, named `{name}` there.",
            verdict=FIXED_VERDICT,
        )
        lean = (
            f"{FILE_HEADER}{form.definitions}\n\n{form.preamble}\n\n"
            f"{form.statement}\n"
        )
        result = self._check("fixed_statement", lean)
        self._decision("fixed_statement", compiles=result.ok, name=name)
        return form if result.ok else None

    def choose_side(self, form: Formalization) -> Formalization:
        """The captain judges whether the statement holds. Judged false, the
        run's theorem becomes its negation, and every later stage proves
        that. The choice is kept as `side.md`, so that a resumed run keeps
        it."""
        path = self.result_dir / "side.md"
        if not path.exists():
            reply = self.agents.complete(
                prompts.SIDE_TEMPLATE.format(
                    informal_statement=self.row["informal_statement"],
                    definitions=self._own_definitions(form),
                    library=self.library or "(none)",
                    statement=form.statement,
                ),
                role="captain",
                phase="side",
                system_prompt=prompts.CAPTAIN_SYSTEM,
            )
            side = extract_tag(reply, "side").strip().lower()
            side = "disprove" if side == "disprove" else "prove"
            reason = extract_tag(reply, "reason").strip()
            path.write_text(f"{side}\n\n{reason}\n")
            self._decision("side_chosen", side=side)
        if path.read_text().split()[0] != "disprove":
            return form
        signature = negation(form.signature)
        lean = (
            f"{FILE_HEADER}{form.definitions}\n\n{form.preamble}\n\n"
            f"theorem {form.theorem_name}{signature}:= by sorry\n"
        )
        if not self._check("negated_statement", lean).ok:
            self._decision("negation_does_not_compile")
            return form
        form.signature = signature
        self.row["side"] = "disprove"
        self.row["informal_statement"] = NEGATED_STATEMENT.format(
            self.row["informal_statement"]
        )
        logger.info("proving the negation of the statement")
        return form

    def proof_for(self, reader: str) -> str:
        """The informal proof as `reader` ("judge" or "review") sees it."""
        proof = self.row["informal_proof"]
        if self.config.mode != "prove":
            return proof
        if self.config.gaps and reader == "judge":
            proof = f"{prompts.GAPS_JUDGE_TEMPLATE}\n{proof}"
        return f"{DRAFT_NOTES[reader]}\n\n{proof}"

    def redraft(self, form: Formalization, findings: str) -> str:
        """A revised draft after failed or refuted lemmas. Earlier drafts are
        kept as draft_<n>.md; draft.md is the current one."""
        path = self.result_dir / "draft.md"
        reply = self.agents.complete(
            prompts.REDRAFT_TEMPLATE.format(
                informal_statement=self.row["informal_statement"],
                statement=form.statement,
                previous=self.row["informal_proof"],
                findings=findings,
            ),
            role="captain",
            phase="redraft",
            system_prompt=prompts.CAPTAIN_SYSTEM,
        )
        proof = extract_tag(reply, "proof").strip()
        if not proof:
            return self.row["informal_proof"]
        number = len(list(self.result_dir.glob("draft_*.md"))) + 1
        if path.exists():
            path.rename(self.result_dir / f"draft_{number}.md")
        path.write_text(proof + "\n")
        self._decision("proof_redrafted", draft=number + 1, chars=len(proof))
        logger.info(f"redrafted the informal proof (draft {number + 1})")
        return proof

    def draft_proof(self, form: Formalization) -> str:
        """The captain's informal proof of the formalization, drafted once."""
        path = self.result_dir / "draft.md"
        if path.exists():
            return path.read_text()
        reply = self.agents.complete(
            prompts.DRAFT_TEMPLATE.format(
                informal_statement=self.row["informal_statement"],
                definitions=self._own_definitions(form),
                library=self.library or "(none)",
                statement=form.statement,
            ),
            role="captain",
            phase="draft",
            system_prompt=prompts.CAPTAIN_SYSTEM,
        )
        proof = extract_tag(reply, "proof").strip()
        if not proof:
            raise ValueError("the draft has no <proof>")
        path.write_text(proof + "\n")
        self._decision("proof_drafted", chars=len(proof))
        logger.info(f"drafted an informal proof of {len(proof)} characters")
        return proof
