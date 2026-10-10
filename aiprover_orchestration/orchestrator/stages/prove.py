"""Proving the sketch's lemmas with chat solvers.

Each pending lemma is attacked by `workers` independent chains. A chain
proposes a proof, receives Lean errors and repairs; the first proof that
compiles is kept and the other chains of that lemma stop. With an AIProver
solver the lemmas go to AIProver jobs instead (`stages/aiprover.py`).

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Proof revision from Lean compiler feedback: Goedel-Prover-V2
  (Lin et al., ICLR 2026), https://github.com/Goedel-LM/Goedel-Prover-V2
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from ...agents.aiprover import AIProverAgent
from ...lean.checker import forbidden_constructs
from ...lean.text import (
    declaration_names,
    drop_imports,
    indent,
)
from .. import prompts
from ..structures import (
    Formalization,
    Lemma,
    Sketch,
    extract_tactics,
    extract_tag,
)

logger = logging.getLogger(__name__)


class ProveStage:

    def prove(self, form: Formalization, sketch: Sketch) -> None:
        pending = [
            (k, lemma)
            for k, lemma in enumerate(sketch.lemmas)
            if not lemma.proved
        ]
        if not pending:
            return
        if isinstance(self.agents.agents["solver"], AIProverAgent):
            self._prove_with_aiprover(form, sketch, pending)
            return
        solved = {lemma.name: threading.Event() for _, lemma in pending}
        result_lock = threading.Lock()

        def chain(k: int, lemma: Lemma, worker: int) -> None:
            available = (
                "\n\n".join(
                    f"{earlier.statement} := by sorry"
                    for earlier in sketch.lemmas[:k]
                )
                or "(none)"
            )
            feedback = ""
            for repair in range(self.config.max_repairs + 1):
                if solved[lemma.name].is_set():
                    return
                reply = self.agents.complete(
                    prompts.SOLVER_TEMPLATE.format(
                        definitions=form.definitions,
                        preamble=form.preamble,
                        available_lemmas=available,
                        lemma_statement=lemma.statement,
                        informal_proof=self.row["informal_proof"],
                        feedback=feedback,
                    ),
                    system_prompt=prompts.SOLVER_SYSTEM,
                    role="solver",
                    phase=f"{lemma.name}/w{worker}/r{repair}",
                    lemma=lemma.name,
                    worker=worker,
                    round=repair,
                )
                helpers = drop_imports(extract_tag(reply, "helpers"))
                proof = extract_tactics(reply, "proof")
                attempt = f"{lemma.statement} := by\n{indent(proof)}"
                problems = [
                    f"forbidden construct: {name}"
                    for name in forbidden_constructs(helpers + "\n" + proof)
                ]
                problems += [
                    f"helper `{name}` must be named " f"`{lemma.name}_...`"
                    for name in declaration_names(helpers)
                    if not name.startswith(lemma.name + "_")
                ]
                if not proof:
                    problems.append("empty proof")
                if not problems:
                    body = "\n\n".join(
                        filter(None, [available if k else "", helpers, attempt])
                    )
                    result = self._check(
                        f"{lemma.name}_w{worker}_r{repair}",
                        self._standalone(form, body),
                        lemma=lemma.name,
                        worker=worker,
                        round=repair,
                    )
                    if result.ok:
                        with result_lock:
                            if not solved[lemma.name].is_set():
                                lemma.helpers, lemma.proof = helpers, proof
                                lemma.proved = True
                                solved[lemma.name].set()
                                logger.info(
                                    f"lemma {lemma.name} proved by worker "
                                    f"{worker} at repair {repair}"
                                )
                                self._decision(
                                    "lemma_proved",
                                    lemma=lemma.name,
                                    worker=worker,
                                    round=repair,
                                    helpers=helpers,
                                    proof=proof,
                                )
                        return
                    problems.append(result.error_report())
                else:
                    self._decision(
                        "solver_attempt_rejected",
                        lemma=lemma.name,
                        worker=worker,
                        round=repair,
                        problems=problems,
                    )
                with result_lock:
                    lemma.last_errors = "\n".join(problems)[:3000]
                    lemma.last_attempts = (
                        f"Solver {worker}, repair {repair}\n<lean>\n"
                        + "\n\n".join(filter(None, [helpers, attempt]))
                        + "\n</lean>\nLean errors:\n"
                        + lemma.last_errors
                    )
                feedback = prompts.SOLVER_REPAIR_TEMPLATE.format(
                    helpers=helpers, proof=proof, errors="\n".join(problems)
                )
            logger.info(
                f"worker {worker} exhausted its budget on " f"{lemma.name}"
            )
            self._decision(
                "solver_budget_exhausted",
                lemma=lemma.name,
                worker=worker,
                errors=lemma.last_errors,
                attempts=lemma.last_attempts,
            )

        workers = self.config.workers
        with ThreadPoolExecutor(max_workers=len(pending) * workers) as pool:
            futures = [
                pool.submit(chain, k, lemma, worker)
                for k, lemma in pending
                for worker in range(workers)
            ]
            for future in futures:
                future.result()
