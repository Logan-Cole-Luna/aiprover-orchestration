"""Proving the sketch's lemmas with AIProver jobs, and hand-back.

Each pending lemma goes to AIProver jobs, up to `aiprover_attempts_per_lemma`
completed jobs per lemma statement. Jobs share `aiprover_session_slots`
sessions, weighted towards lemmas with more failures. Every sample's proof
passes the same Lean gate as chat solvers. After `aiprover_handback_after`
failures a lemma is handed back to the captain, who proves, splits, restates
it, or asks for a retry.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Splitting a lemma the prover fails on into subgoals: Hilbert
  (Varambally et al., ICLR 2026), https://github.com/Rose-STL-Lab/ml-hilbert
- [Similar] Refining the plan from failed lemmas: Goedel-Architect (Chung
  et al., ICML 2026; no code released), https://icml.cc/virtual/2026/82541
- [Similar] Spending prover attempts by estimated success and cost:
  Rognvaldsson et al., ICML 2026,
  https://github.com/eth-sri/optimizing-lean-agents
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from ...agents.aiprover import AIProverAgent
from ...agents.base import AgentCallError, Completion
from ...lean.checker import forbidden_constructs
from ...lean.text import (
    drop_imports,
    indent,
    normalize,
    split_declarations,
    strip_leading_by,
)
from .. import knowledge, prompts
from ..extraction import (
    extract_lemma_proof,
    failed_attempt,
    wrapped_lemma_proof,
)
from ..structures import (
    Formalization,
    Lemma,
    Sketch,
    extract_tag,
    extract_tactics,
    failed_lemma_text,
    parse_lemmas,
)

logger = logging.getLogger(__name__)

# Sample states that say nothing about the lemma: the session failed on
# infrastructure or was cancelled.
STOPPED_STATES = ("infra", "cancelled")


class AIProverStage:

    def _prove_with_aiprover(
        self,
        form: Formalization,
        sketch: Sketch,
        pending: list[tuple[int, Lemma]],
    ) -> None:
        """Prove each pending lemma with AIProver jobs.

        A job receives the definitions and earlier lemmas as fixed context
        and the lemma as fixed statement; the first sample that passes the
        Lean gate proves the lemma. A split from a hand-back adds new lemmas
        to this phase.
        """
        solver: AIProverAgent = self.agents.agents["solver"]
        # Guards sketch.lemmas, lemma statements and the session counts.
        sketch_lock = threading.Lock()
        sessions_in_flight: dict[str, int] = {}
        in_handback: set[str] = set()  # lemmas waiting on the captain
        max_attempts = max(1, self.config.aiprover_attempts_per_lemma)
        concurrency = self.config.aiprover_lemma_concurrency or len(pending)

        def weight(lemma: Lemma) -> int:
            return 1 + lemma.attempts

        def allocate(lemma: Lemma, servers: int) -> int:
            """Sessions for the next job on `lemma`: its weighted share of the
            free slots, `aiprover_session_slots` per model server, against
            the lemmas that could start alongside it."""
            slots = self.config.aiprover_session_slots
            if not slots:
                return self.config.workers
            slots *= servers
            free = slots - sum(sessions_in_flight.values())
            waiting = sorted(
                (
                    other
                    for other in sketch.lemmas
                    if other is not lemma
                    and not other.proved
                    and other.name not in sessions_in_flight
                    and other.name not in in_handback
                    and other.attempts < max_attempts
                ),
                key=weight,
                reverse=True,
            )
            rivals = waiting[
                : max(0, concurrency - len(sessions_in_flight) - 1)
            ]
            share = (
                free
                * weight(lemma)
                // (weight(lemma) + sum(map(weight, rivals)))
            )
            return max(1, min(free, share))

        def maybe_handback(lemma: Lemma) -> None:
            after = self.config.aiprover_handback_after
            if (
                not after
                or lemma.proved
                or lemma.handed_back
                or lemma.attempts - lemma.reviewed_at < after
                or stopped.is_set()
            ):
                return
            if not lemma.last_attempts and not lemma.knowledge:
                # No session left code or notes: a setup failure (clock,
                # stalled replies); retry before asking the captain.
                logger.info(
                    f"{lemma.name}: no code in the last attempt; "
                    f"hand-back deferred"
                )
            elif self.agents.claude_budget_left() < 2:
                logger.info(
                    f"{lemma.name}: Claude budget too low for a " f"hand-back"
                )
            else:
                # A hand-back takes minutes against a job's 90, so the lemma
                # gives up its share of the slots meanwhile.
                with sketch_lock:
                    in_handback.add(lemma.name)
                try:
                    self._handback(form, sketch, lemma, sketch_lock, submit)
                finally:
                    with sketch_lock:
                        in_handback.discard(lemma.name)

        def attempt(lemma: Lemma) -> None:
            # The hand-back comes before the next job, so a lemma restored
            # with enough failures (after a resume) goes to the captain first.
            while not lemma.proved and not stopped.is_set():
                maybe_handback(lemma)
                if lemma.proved or lemma.attempts >= max_attempts:
                    break
                servers = solver.servers()
                with sketch_lock:
                    samples = allocate(lemma, servers)
                    sessions_in_flight[lemma.name] = samples
                try:
                    one_job(lemma, samples)
                finally:
                    with sketch_lock:
                        sessions_in_flight.pop(lemma.name, None)

        def context_for(lemma: Lemma) -> tuple[str, set[str]]:
            with sketch_lock:
                earlier = sketch.lemmas[: sketch.lemmas.index(lemma)]
                stubs = "\n\n".join(
                    f"{other.statement} := by sorry" for other in earlier
                )
            fixed = split_declarations(self._standalone(form, stubs))
            return stubs, {name for _, name, _ in fixed}

        def one_job(lemma: Lemma, samples: int) -> None:
            stubs, fixed_names = context_for(lemma)
            context = self._standalone(form, stubs).rstrip() + "\n"
            statement = f"{lemma.statement} := by\n  sorry\n"
            theorem_text = prompts.AIPROVER_LEMMA_TEMPLATE.format(
                lemma_name=lemma.name,
                informal_statement=self.row["informal_statement"],
            )
            key = (lemma.name, normalize(lemma.statement))
            # A job of this statement cut off by a lost server or an
            # interruption continues its sessions instead of starting new
            # ones.
            with sketch_lock:
                resume_job = self.state.resumable_jobs.pop(key, None)
            logger.info(
                f"AIProver job on {lemma.name}: "
                + (
                    f"resuming {resume_job}"
                    if resume_job
                    else f"{samples} session(s)"
                )
                + f", attempt {lemma.attempts + 1}/{max_attempts}"
            )
            job = solver.solve(
                name=f"{self.run_id}_{lemma.name}"[:60],
                theorem_text=theorem_text,
                proof_text=self.row["informal_proof"],
                context=context,
                statement=statement,
                samples=samples,
                work_dir=self.temp_dir / "aiprover",
                resume_job=resume_job,
                hint=knowledge.hint(lemma.knowledge),
                on_start=lambda job_id: self._decision(
                    "aiprover_job_started",
                    lemma=lemma.name,
                    statement=key[1],
                    aiprover_job=job_id,
                ),
            )
            ranked = sorted(
                job.samples, key=lambda sample: sample.status != "verified"
            )
            best = next((sample.lean for sample in ranked if sample.lean), "")
            self.agents.record(
                "solver",
                f"{lemma.name}/aiprover",
                job.problem,
                Completion(text=best, error=job.error),
                job.seconds,
                lemma=lemma.name,
                worker=0,
                round=0,
                aiprover_job=job.job,
                samples=[
                    {
                        "sample": sample.index,
                        "status": sample.status,
                        "turns": sample.turns,
                        "tool_calls": sample.tool_calls,
                        "elapsed_sec": sample.elapsed_sec,
                        "ending": sample.ending,
                        "problems": sample.problems[:5],
                        "check": sample.check,
                        "lean": sample.lean,
                        "session": sample.session,
                        "reasoning": sample.reasoning,
                    }
                    for sample in job.samples
                ],
            )
            errors = [job.error] if job.error else []
            with sketch_lock:
                sketch_names = {other.name for other in sketch.lemmas}
            for sample in ranked:
                if gate_sample(
                    lemma, sample, stubs, fixed_names, sketch_names, errors
                ):
                    return
            # A lost model server is an infrastructure failure, not a failed
            # lemma: the run stops and resumes this lemma instead of
            # replanning.
            if not solver.endpoint_up():
                raise AgentCallError(
                    f"AIProver endpoint unavailable during {lemma.name}"
                )
            # A job with sessions cut off by infrastructure (endpoint, model
            # name) or a cancellation is incomplete, not a failed attempt: the
            # run stops and its resume continues those sessions.
            if any(sample.status in STOPPED_STATES for sample in job.samples):
                endings = sorted(
                    {sample.ending for sample in job.samples if sample.ending}
                )
                raise AgentCallError(
                    f"AIProver job {job.job} on {lemma.name}: sessions cut off "
                    f"({'; '.join(endings)[:300]})"
                )
            lemma.attempts += 1
            lemma.last_errors = "\n\n".join(errors)[:3000]
            # The samples' own code, most complete first, for the captain.
            codes = [
                failed_attempt(sample, lemma.name, fixed_names)
                for sample in job.samples
            ]
            attempts = sorted(filter(None, codes), key=lambda entry: entry[0])
            lemma.last_attempts = "\n\n".join(text for _, text in attempts)
            lemma.knowledge = knowledge.carry(
                lemma.knowledge,
                [
                    knowledge.session_notes(
                        sample, lemma.attempts, code[1] if code else ""
                    )
                    for sample, code in zip(job.samples, codes)
                ],
            )
            logger.info(
                f"AIProver job {job.job} did not prove {lemma.name} "
                f"(attempt {lemma.attempts}/{max_attempts}): "
                f"{[sample.status for sample in job.samples]}"
            )
            self._decision(
                "solver_budget_exhausted",
                lemma=lemma.name,
                worker=0,
                aiprover_job=job.job,
                errors=lemma.last_errors,
                attempts=lemma.last_attempts,
                knowledge=lemma.knowledge,
            )

        def gate_sample(
            lemma: Lemma,
            sample,
            stubs: str,
            fixed_names: set[str],
            sketch_names: set[str],
            errors: list[str],
        ) -> bool:
            """Prove `lemma` from one sample; on failure append to `errors`.

            The proof is spliced under the sketch's statement, then, if that
            fails, the answer's whole theorem is kept as a helper.
            """
            extracted = extract_lemma_proof(
                sample.lean, lemma.name, fixed_names
            )
            if extracted is None:
                errors.append(
                    f"sample {sample.index} ({sample.status}): "
                    f"no proof of `{lemma.name}` in the answer"
                )
                return False
            alias = f"{lemma.name}_aiprover_s{sample.index}"
            candidates = [("", extracted)]
            wrapped = wrapped_lemma_proof(
                sample.lean, lemma.name, fixed_names, alias
            )
            if wrapped:
                candidates.append(("_kept", wrapped))
            problems = []
            for suffix, (helpers, proof) in candidates:
                proof = strip_leading_by(proof)
                found = [
                    f"forbidden construct: {name}"
                    for name in forbidden_constructs(helpers + "\n" + proof)
                ]
                found += [
                    f"helper `{name}` collides with a sketch lemma"
                    for _, name, _ in split_declarations(helpers)
                    if name in sketch_names
                ]
                if not found:
                    body = "\n\n".join(
                        filter(
                            None,
                            [
                                stubs,
                                helpers,
                                f"{lemma.statement} := by\n{indent(proof)}",
                            ],
                        )
                    )
                    result = self._check(
                        alias + suffix,
                        self._standalone(form, body),
                        lemma=lemma.name,
                        worker=sample.index,
                        round=0,
                    )
                    if result.ok:
                        lemma.helpers, lemma.proof = helpers, proof
                        lemma.proved = True
                        logger.info(
                            f"lemma {lemma.name} proved by AIProver sample "
                            f"{sample.index} ({sample.status}"
                            f"{', own statement kept' if suffix else ''})"
                        )
                        self._decision(
                            "lemma_proved",
                            lemma=lemma.name,
                            worker=sample.index,
                            round=0,
                            helpers=helpers,
                            proof=proof,
                        )
                        return True
                    found.append(result.error_report())
                problems += found
            errors.append(
                f"sample {sample.index} ({sample.status}): "
                + "\n".join(problems)
            )
            return False

        # After a failure (lost endpoint, interruption), lemmas not yet
        # started are skipped rather than run against a dead server. The
        # flag is set by the failing thread itself, before its worker is
        # reused.
        stopped = threading.Event()

        def guarded(lemma: Lemma) -> None:
            if stopped.is_set():
                return
            try:
                attempt(lemma)
            except BaseException:
                stopped.set()
                raise

        futures = []
        futures_lock = threading.Lock()

        def submit(lemma: Lemma) -> None:
            with futures_lock:
                futures.append(pool.submit(guarded, lemma))

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for _, lemma in pending:
                submit(lemma)
            try:
                done = 0
                while True:
                    with futures_lock:
                        if done == len(futures):
                            break
                        future = futures[done]
                    future.result()
                    done += 1
            except BaseException:
                stopped.set()
                raise

    def _handback(
        self,
        form: Formalization,
        sketch: Sketch,
        lemma: Lemma,
        sketch_lock: threading.Lock,
        submit,
    ) -> None:
        """Hand a lemma the solvers keep failing on back to the captain.

        The captain proves it, splits it into new lemmas (handed to solvers
        through `submit`), restates it, or asks for a retry. Every proof and
        statement it proposes is checked by Lean before it is applied; one
        repair is allowed.
        """
        lemma.handed_back = True
        with sketch_lock:
            position = sketch.lemmas.index(lemma)
            listing = "\n\n".join(
                f"{other.statement} := by sorry"
                + ("  -- proved" if other.proved else "")
                + ("  -- this lemma" if other is lemma else "")
                for other in sketch.lemmas
            )
            earlier = sketch.lemmas[:position]
            stubs = "\n\n".join(
                f"{other.statement} := by sorry" for other in earlier
            )
            names = {other.name for other in sketch.lemmas}
            name_pattern = rf"(?<![\w.']){re.escape(lemma.name)}(?![\w'])"
            used_by_proofs = any(
                re.search(name_pattern, other.helpers + "\n" + other.proof)
                for other in sketch.lemmas
                if other.proved
            )
        prompt = prompts.HANDBACK_TEMPLATE.format(
            attempts=lemma.attempts,
            lemma_name=lemma.name,
            definitions=form.definitions,
            preamble=form.preamble,
            lemmas=listing,
            main_proof=sketch.main_proof,
            informal_proof=self.row["informal_proof"],
            failed=failed_lemma_text(lemma),
            knowledge=lemma.knowledge or "(none)",
        )
        feedback = ""
        for repair in range(2):
            if repair and self.agents.claude_budget_left() < 1:
                break
            reply = self._captain(
                prompt + feedback, phase=f"handback/{lemma.name}"
            )
            diagnosis = extract_tag(reply, "diagnosis")
            helpers = drop_imports(extract_tag(reply, "helpers"))
            proof = extract_tactics(reply, "proof")
            split_block = drop_imports(extract_tag(reply, "split"))
            restated = extract_tag(reply, "restate")
            if "<knowledge>" in reply:
                # Applied with whichever action follows: the next sessions
                # on this lemma, or its new statement, start from it.
                lemma.knowledge = extract_tag(reply, "knowledge")
            problems = []
            if restated:
                new = parse_lemmas(
                    restated if ":=" in restated else restated + " := by sorry"
                )
                if len(new) != 1 or new[0].name != lemma.name:
                    problems.append(
                        f"<restate> must state exactly "
                        f"`theorem {lemma.name} ...`"
                    )
                elif used_by_proofs:
                    problems.append(
                        f"proved lemmas use `{lemma.name}`; "
                        f"it cannot be restated"
                    )
                else:
                    with sketch_lock:
                        candidate = Sketch(
                            [
                                new[0] if other is lemma else other
                                for other in sketch.lemmas
                            ],
                            sketch.main_proof,
                        )
                    result = self._check(
                        f"{lemma.name}_restated",
                        self._standalone(
                            form, self._sketch_body(form, candidate)
                        ),
                        lemma=lemma.name,
                    )
                    if result.ok:
                        old = lemma.statement
                        with sketch_lock:
                            lemma.statement = new[0].statement
                            lemma.attempts, lemma.handed_back = 0, False
                            lemma.reviewed_at = 0
                            if "<knowledge>" not in reply:
                                lemma.knowledge = ""
                        logger.info(f"{lemma.name} restated by the captain")
                        self._decision(
                            "lemma_handback",
                            lemma=lemma.name,
                            action="restate",
                            diagnosis=diagnosis,
                            statement=lemma.statement,
                            previous_statement=old,
                            knowledge=lemma.knowledge,
                        )
                        return
                    problems.append(result.error_report())
            elif proof:
                new_lemmas = parse_lemmas(split_block) if split_block else []
                clashes = [
                    other.name
                    for other in new_lemmas
                    if other.name in names or other.name == lemma.name
                ]
                problems += [
                    f"new lemma `{name}` reuses an existing name"
                    for name in clashes
                ]
                problems += [
                    f"forbidden construct: {name}"
                    for name in forbidden_constructs(
                        helpers
                        + "\n"
                        + proof
                        + "\n"
                        + re.sub(r"\bsorry\b", "", split_block)
                    )
                ]
                if not problems:
                    body = "\n\n".join(
                        filter(
                            None,
                            [
                                stubs,
                                *(
                                    f"{other.statement} := by sorry"
                                    for other in new_lemmas
                                ),
                                helpers,
                                f"{lemma.statement} := by\n{indent(proof)}",
                            ],
                        )
                    )
                    result = self._check(
                        f"{lemma.name}_captain",
                        self._standalone(form, body),
                        lemma=lemma.name,
                        worker="captain",
                    )
                    if result.ok:
                        with sketch_lock:
                            for offset, other in enumerate(new_lemmas):
                                sketch.lemmas.insert(position + offset, other)
                            lemma.helpers, lemma.proof = helpers, proof
                            lemma.proved = True
                        action = "split" if new_lemmas else "proof"
                        new_names = [other.name for other in new_lemmas]
                        logger.info(
                            f"{lemma.name}: captain {action}"
                            + (f" into {new_names}" if new_lemmas else "")
                        )
                        self._decision(
                            "lemma_handback",
                            lemma=lemma.name,
                            action=action,
                            diagnosis=diagnosis,
                            new_lemmas=[
                                other.statement for other in new_lemmas
                            ],
                        )
                        self._decision(
                            "lemma_proved",
                            lemma=lemma.name,
                            worker="captain",
                            round=0,
                            helpers=helpers,
                            proof=proof,
                        )
                        for other in new_lemmas:
                            submit(other)
                        return
                    problems.append(result.error_report())
            elif "<retry" in reply:
                logger.info(f"{lemma.name}: captain asks for a retry")
                # The captain reviews the lemma again after as many further
                # failures as led to this hand-back.
                with sketch_lock:
                    lemma.handed_back = False
                    lemma.reviewed_at = lemma.attempts
                self._decision(
                    "lemma_handback",
                    lemma=lemma.name,
                    action="retry",
                    diagnosis=diagnosis,
                    attempts=lemma.attempts,
                    knowledge=lemma.knowledge,
                )
                return
            else:
                problems.append("the reply names no action")
            logger.info(
                f"{lemma.name}: hand-back reply rejected: "
                f"{problems[0][:200]}"
            )
            feedback = prompts.HANDBACK_REPAIR_TEMPLATE.format(
                reply=reply, errors="\n".join(problems)[:6000]
            )
        self._decision(
            "lemma_handback",
            lemma=lemma.name,
            action="rejected",
            diagnosis="",
            problems=problems[:5],
        )
