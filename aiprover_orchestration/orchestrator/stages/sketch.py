"""Proof sketch and replanning.

The captain decomposes the proof into lemmas (stated in full, left as
`sorry`) and a proof of `solution` from them; the sketch must compile. After
failed lemmas, the captain revises the sketch with the solvers' failed code
and errors; proved lemmas whose statements are unchanged keep their proofs.

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
"""

import logging
import re

from ...lean.checker import forbidden_constructs
from ...lean.text import drop_imports, indent, normalize
from .. import prompts
from ..config import MAIN_NAME
from ..structures import (
    Formalization,
    Sketch,
    dependency_order,
    extract_tag,
    extract_tactics,
    failed_lemma_text,
    missing_lemmas,
    parse_lemmas,
)

logger = logging.getLogger(__name__)


class SketchStage:

    def sketch(self, form: Formalization, replan_feedback: str = "") -> Sketch:
        feedback = replan_feedback
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
            lemma_block = drop_imports(extract_tag(reply, "lemmas"))
            main_proof = extract_tactics(reply, "main_proof")
            lemmas = parse_lemmas(lemma_block)
            names = [lemma.name for lemma in lemmas]
            problems = [
                f"forbidden construct in main proof: {name}"
                for name in forbidden_constructs(main_proof)
            ]
            problems += [
                f"forbidden construct in lemma statements: {name}"
                for name in forbidden_constructs(
                    re.sub(r"\bsorry\b", "", lemma_block)
                )
                if name != "sorry"
            ]
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
                    self._decision(
                        "sketch_accepted",
                        repair=repair,
                        lemmas=[lemma.statement for lemma in lemmas],
                        main_proof=main_proof,
                    )
                    return candidate
                problems.append(result.error_report())
            logger.info(f"sketch repair {repair}: {problems[0][:200]}")
            self._decision("sketch_rejected", repair=repair, problems=problems)
            feedback = replan_feedback + prompts.SKETCH_REPAIR_TEMPLATE.format(
                lemmas=lemma_block,
                main_proof=main_proof,
                errors="\n".join(problems),
            )
        raise RuntimeError("sketch did not compile within the repair budget")

    def _sketch_body(
        self, form: Formalization, sketch: Sketch, use_proofs: bool = False
    ) -> str:
        parts = []
        lemmas = (
            dependency_order(sketch.lemmas) if use_proofs else sketch.lemmas
        )
        for lemma in lemmas:
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
        feedback = prompts.REPLAN_FEEDBACK_TEMPLATE.format(
            lemmas="\n\n".join(
                f"{lemma.statement} := by sorry" for lemma in sketch.lemmas
            ),
            main_proof=sketch.main_proof,
            proved="\n\n".join(lemma.statement for lemma in proved) or "(none)",
            failed="\n\n".join(failed_lemma_text(lemma) for lemma in failed),
        )
        self._decision(
            "replan_resumed" if resumed else "replan",
            proved=[lemma.name for lemma in proved],
            failed=[lemma.name for lemma in failed],
        )
        new_sketch = self.sketch(form, feedback)
        proofs = {normalize(lemma.statement): lemma for lemma in proved}
        kept = {
            lemma.name
            for lemma in new_sketch.lemmas
            for old in sketch.lemmas
            if old.name == lemma.name
            and normalize(old.statement) == normalize(lemma.statement)
        }
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
