"""Proving the sketch's lemmas with AIProver jobs.

Each pending lemma goes to AIProver jobs, up to `aiprover_attempts_per_lemma`
completed jobs per lemma statement over the run, across sketches and resumes
(`ResumeState.attempts`). Jobs share `aiprover_session_slots` sessions,
weighted towards lemmas with more failures. Every sample's proof passes the
same Lean gate as chat solvers. After `aiprover_handback_after` failures a
lemma is handed back to the captain, and several stuck leaves under one
decision start a conflict analysis (`stages/handback.py`). A lemma whose
statement has used its attempts is handed back once more (early revision)
instead of waiting for the replan. A refuted lemma
ends the phase once the running jobs return, and the sketch is revised. A
later lemma whose statement equals a proved one retracted by a backjump
reuses its proof.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Spending prover attempts by estimated success and cost:
  Rognvaldsson et al., ICML 2026,
  https://github.com/eth-sri/optimizing-lean-agents
"""

import logging
import math
import re
import threading
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from ...agents.aiprover import AIProverAgent, AIProverSample, sample_record
from ...agents.base import AgentCallError, AgentRefusal, Completion
from ...lean.checker import forbidden_problems
from ...lean.text import normalize, split_declarations, strip_leading_by
from .. import cdcl, knowledge, prompts
from ..extraction import (
    extract_lemma_proof,
    failed_attempt,
    helper_names,
    prefixed_helpers,
    proved_helper_names,
    wrapped_lemma_proof,
)
from ..structures import (
    Formalization,
    Lemma,
    Sketch,
    missing_lemmas,
    stub_listing,
)

logger = logging.getLogger(__name__)

# Sample states that say nothing about the lemma: the session failed on
# infrastructure or was cancelled.
STOPPED_STATES = ("infra", "cancelled")
# Interval at which running jobs receive the session slots freed meanwhile.
REBALANCE_SECONDS = 60
# Lemma jobs at once without a concurrency limit (threads that mostly wait).
MAX_LEMMA_JOBS = 256
# Failed samples' code kept for the captain and the replan: the most
# complete ones, each cut to a length.
MAX_ATTEMPTS_KEPT = 2
MAX_ATTEMPT_CHARS = 6000


@dataclass(eq=False)
class Phase:
    """One AIProver phase and the state its threads share; `lock` guards
    the sketch's lemmas and statements and the collections below."""

    form: Formalization
    sketch: Sketch
    solver: AIProverAgent
    # Declarations found and searches that failed, over the run's sessions.
    mathlib: knowledge.MathlibMap
    # The run's own declarations, which sessions must not look for in
    # Mathlib.
    run_names: set[str]
    # Hand-backs and conflict analyses so far in the run, including earlier
    # sketches and runs before a resume.
    handbacks: int
    # Conflicts analysed: (decision, statement, stuck leaves).
    analysed: set[tuple]
    # Statements revised after their last attempt: (lemma, statement).
    revised: set[tuple] = field(default_factory=set)
    # Captain retries per lemma since its last restatement.
    retries: Counter = field(default_factory=Counter)
    # The captain's informal proof of each lemma statement for its jobs:
    # (lemma, statement): direction.
    directions: dict[tuple, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    sessions_in_flight: dict[str, int] = field(default_factory=dict)
    running_jobs: dict[str, str] = field(default_factory=dict)  # lemma: job
    in_handback: set[str] = field(default_factory=set)
    in_analysis: set[str] = field(default_factory=set)  # decisions
    # Proved lemmas retracted by a backjump, by normalized statement, for
    # reuse, and the names of every retracted lemma.
    retracted: dict[str, Lemma] = field(default_factory=dict)
    retracted_names: set[str] = field(default_factory=set)
    # Set once a lemma is refuted: the sketch must change, so no further job
    # or hand-back starts and the phase ends with the running jobs.
    refuted: threading.Event = field(default_factory=threading.Event)
    # Set after a failure (lost endpoint, interruption): lemmas not yet
    # started are skipped rather than run against a dead server.
    stopped: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    futures: list[Future] = field(default_factory=list)
    futures_lock: threading.Lock = field(default_factory=threading.Lock)
    pool: ThreadPoolExecutor | None = None

    def present(self, lemma: Lemma) -> bool:
        """False once a backjump has retracted `lemma` (identity, not
        equality: lemmas are dataclasses)."""
        return any(other is lemma for other in self.sketch.lemmas)

    def halted(self) -> bool:
        return self.stopped.is_set() or self.refuted.is_set()


def weight(lemma: Lemma) -> int:
    """A lemma's share of the session slots: 1 + its failed attempts."""
    return 1 + lemma.attempts


class AIProverStage:

    def _aiprover_phase(self, form: Formalization, sketch: Sketch) -> Phase:
        steps = self.trace.document["steps"]
        phase = Phase(
            form=form,
            sketch=sketch,
            solver=self.agents.agents["solver"],
            mathlib=knowledge.MathlibMap.from_steps(steps),
            run_names=knowledge.declared_names(
                form.definitions + "\n" + form.preamble
            ),
            handbacks=sum(
                step["kind"] == "decision"
                and step.get("event") in ("lemma_handback", "conflict")
                for step in steps
            ),
            analysed={
                (
                    step["lemma"],
                    normalize(step["statement"]),
                    frozenset(step.get("stuck") or []),
                )
                for step in steps
                if step["kind"] == "decision"
                and step.get("event") == "conflict"
            },
            revised={
                (step["lemma"], normalize(step["statement"]))
                for step in steps
                if step["kind"] == "decision"
                and step.get("event") == "early_revision"
            },
            directions={
                (step["lemma"], normalize(step["statement"])): step["direction"]
                for step in steps
                if step["kind"] == "decision"
                and step.get("event") == "lemma_direction"
            },
        )
        for step in steps:
            if step["kind"] == "decision" and step.get("event") == (
                "lemma_handback"
            ):
                if step.get("action") == "retry":
                    phase.retries[step["lemma"]] += 1
                elif step.get("action") == "restate":
                    phase.retries.pop(step["lemma"], None)
        if any(lemma.refutation for lemma in sketch.lemmas):
            phase.refuted.set()
        return phase

    def _retries_spent(self, phase: Phase, lemma: Lemma) -> int:
        """The lemma's retries if they have reached
        `aiprover_max_retries`, else 0."""
        limit = self.config.aiprover_max_retries
        retries = phase.retries[lemma.name]
        return retries if limit and retries >= limit else 0

    def _prove_with_aiprover(
        self,
        form: Formalization,
        sketch: Sketch,
        pending: list[tuple[int, Lemma]],
    ) -> None:
        """Prove each pending lemma with AIProver jobs. A split from a
        hand-back adds new lemmas to this phase."""
        phase = self._aiprover_phase(form, sketch)
        # Concurrent lemma jobs; 0 for no limit, under which a lemma added
        # by a split starts at once rather than after a running lemma.
        concurrency = self.config.aiprover_lemma_concurrency or MAX_LEMMA_JOBS
        with ThreadPoolExecutor(max_workers=concurrency) as phase.pool:
            for _, lemma in pending:
                self._submit(phase, lemma)
            done = 0
            try:
                while True:
                    phase.finished.clear()
                    background = [
                        threading.Thread(
                            target=target, args=(phase,), daemon=True
                        )
                        for target in (self._rebalance, self._watch_conflicts)
                    ]
                    for thread in background:
                        thread.start()
                    while True:
                        with phase.futures_lock:
                            if done == len(phase.futures):
                                break
                            future = phase.futures[done]
                        future.result()
                        done += 1
                    # A conflict analysis in progress may still retract
                    # lemmas and submit a restated decision: the phase ends
                    # once it has returned without adding work, so that no
                    # thread changes the sketch after the phase.
                    phase.finished.set()
                    for thread in background:
                        thread.join()
                    with phase.futures_lock:
                        if done == len(phase.futures):
                            break
            except BaseException:
                phase.stopped.set()
                # The run stops: the jobs still running are cancelled rather
                # than awaited, and their sessions resume with the run.
                with phase.lock:
                    jobs = list(phase.running_jobs.values())
                for job in jobs:
                    phase.solver.cancel(job)
                raise
            finally:
                phase.finished.set()

    # Scheduling ------------------------------------------------------------

    def _submit(self, phase: Phase, lemma: Lemma) -> None:
        if self._reuse(phase, lemma):
            return
        with phase.futures_lock:
            phase.futures.append(
                phase.pool.submit(self._guarded, phase, lemma)
            )

    def _guarded(self, phase: Phase, lemma: Lemma) -> None:
        """Attempt `lemma`; a failure stops the phase from the failing
        thread itself, before its worker is reused."""
        if phase.stopped.is_set():
            return
        try:
            self._attempt(phase, lemma)
        except BaseException:
            phase.stopped.set()
            raise

    def _attempt(self, phase: Phase, lemma: Lemma) -> None:
        """Jobs on `lemma` until it is proved or out of attempts, then one
        early revision; jobs resume if it leaves attempts (a restatement or
        a retry)."""
        self._jobs(phase, lemma)
        while self._revise(phase, lemma):
            self._jobs(phase, lemma)

    def _jobs(self, phase: Phase, lemma: Lemma) -> None:
        """Jobs on `lemma` until it is proved or out of attempts. The
        hand-back comes before the next job, so a lemma restored with enough
        failures (after a resume) goes to the captain first."""
        while not lemma.proved and not phase.stopped.is_set():
            self._maybe_handback(phase, lemma)
            if (
                lemma.proved
                or self._exhausted(lemma)
                or phase.refuted.is_set()
                or not phase.present(lemma)
            ):
                break
            servers = phase.solver.servers()
            with phase.lock:
                samples = self._allocate(phase, lemma, servers)
                phase.sessions_in_flight[lemma.name] = samples
            try:
                self._one_job(phase, lemma, samples)
            finally:
                with phase.lock:
                    phase.sessions_in_flight.pop(lemma.name, None)
                    phase.running_jobs.pop(lemma.name, None)

    def _startable(self, phase: Phase, lemma: Lemma) -> bool:
        """An unproved lemma without a job that could start one."""
        return (
            not lemma.proved
            and lemma.name not in phase.sessions_in_flight
            and lemma.name not in phase.in_handback
            and not self._exhausted(lemma)
            and not phase.refuted.is_set()
        )

    def _allocate(self, phase: Phase, lemma: Lemma, servers: int) -> int:
        """Sessions for the next job on `lemma` (call with `phase.lock`
        held): its weighted share of the free slots,
        `aiprover_session_slots` per model server, against the lemmas that
        could start alongside it."""
        slots = self.config.aiprover_session_slots
        if not slots:
            return self.config.workers
        concurrency = self.config.aiprover_lemma_concurrency or MAX_LEMMA_JOBS
        free = slots * servers - sum(phase.sessions_in_flight.values())
        waiting = sorted(
            (
                other
                for other in phase.sketch.lemmas
                if other is not lemma and self._startable(phase, other)
            ),
            key=weight,
            reverse=True,
        )
        rivals = waiting[
            : max(0, concurrency - len(phase.sessions_in_flight) - 1)
        ]
        share = (
            free * weight(lemma) // (weight(lemma) + sum(map(weight, rivals)))
        )
        limit = self.config.aiprover_job_sessions or math.inf
        return max(1, min(free, share, limit))

    def _rebalance(self, phase: Phase) -> None:
        """Give running jobs the session slots freed while they run (a
        finished session, a proved lemma), which a job sized at its start
        cannot receive. Slots go to new jobs first: running jobs grow only
        while no lemma waits to start one, only into slots free on both the
        model servers and this machine, and up to `aiprover_job_sessions`
        sessions each."""
        slots = self.config.aiprover_session_slots
        limit = self.config.aiprover_job_sessions or math.inf
        solver = phase.solver
        while slots and not phase.finished.wait(REBALANCE_SECONDS):
            if phase.stopped.is_set():
                return
            with phase.lock:
                jobs = dict(phase.running_jobs)
                if not jobs or any(
                    self._startable(phase, lemma)
                    for lemma in phase.sketch.lemmas
                ):
                    continue
            live = {
                name: solver.live_sessions(job) for name, job in jobs.items()
            }
            free = min(
                slots * solver.servers() - sum(live.values()),
                solver.free_slots(),
            )
            with phase.lock:
                for name, count in live.items():
                    if name in phase.sessions_in_flight:
                        phase.sessions_in_flight[name] = count
                growing = sorted(
                    (
                        lemma
                        for lemma in phase.sketch.lemmas
                        if lemma.name in jobs and not lemma.proved
                    ),
                    key=weight,
                    reverse=True,
                )
            if free < 1 or not growing or phase.refuted.is_set():
                continue
            total = sum(map(weight, growing))
            left = free
            for lemma in growing:
                job = jobs[lemma.name]
                share = min(
                    left,
                    max(1, free * weight(lemma) // total),
                    limit - live[lemma.name],
                )
                added = share > 0 and solver.grow(job, share)
                if not added:
                    continue
                left -= added
                with phase.lock:
                    if lemma.name in phase.sessions_in_flight:
                        phase.sessions_in_flight[lemma.name] += added
                logger.info(f"{lemma.name}: {added} session(s) added to {job}")
                self._decision(
                    "aiprover_job_grown",
                    lemma=lemma.name,
                    aiprover_job=job,
                    sessions=added,
                )
                if left < 1:
                    break

    # The captain: hand-back, conflict analysis, backjump -------------------

    def _maybe_handback(self, phase: Phase, lemma: Lemma) -> None:
        handback_after = self.config.aiprover_handback_after
        self._analyse_conflict(phase, lemma)
        with phase.lock:
            # A leaf under a decision being analysed waits for the analysis,
            # which may retract it.
            waiting = not phase.present(lemma) or any(
                other.name in phase.in_analysis
                for other in cdcl.ancestors(lemma, phase.sketch.lemmas)
            )
        # Retried more often than allowed (before the limit existed).
        over = phase.retries[lemma.name] > self.config.aiprover_max_retries > 0
        if (
            waiting
            or not handback_after
            or lemma.proved
            or lemma.handed_back
            or (
                lemma.attempts - lemma.reviewed_at < handback_after
                and not over
            )
            or phase.halted()
        ):
            return
        if (
            not over
            and not lemma.last_attempts
            and not lemma.knowledge
            and not self._exhausted(lemma)
        ):
            # No session left code or notes: a setup failure (clock, stalled
            # replies); retry before asking the captain. A lemma without
            # attempts left goes to the captain regardless.
            logger.info(
                f"{lemma.name}: no code in the last attempt; "
                f"hand-back deferred"
            )
            return
        if self.agents.claude_budget_left() < 2:
            logger.info(f"{lemma.name}: Claude budget too low for a hand-back")
            return
        limit = self.config.max_handbacks
        # A hand-back takes minutes against a job's 90, so the lemma gives up
        # its share of the slots meanwhile.
        with phase.lock:
            allowed = not limit or phase.handbacks < limit
            if allowed:
                phase.in_handback.add(lemma.name)
                phase.handbacks += 1
        if not allowed:
            logger.info(f"{lemma.name}: hand-back limit of {limit} reached")
            return
        try:
            self._handback(phase, lemma)
            if lemma.refutation:
                phase.refuted.set()
        except AgentRefusal as error:
            # The captain's model refuses this prompt (a safety classifier),
            # so asking again would fail the same way: the lemma continues
            # without a hand-back instead of the run stopping and repeating
            # it on every resume.
            logger.warning(
                f"{lemma.name}: hand-back refused by the model; "
                f"continuing without it"
            )
            lemma.handed_back = True
            self._decision(
                "lemma_handback",
                lemma=lemma.name,
                action="refused",
                diagnosis=str(error)[:300],
            )
        finally:
            with phase.lock:
                phase.in_handback.discard(lemma.name)

    def _revise(self, phase: Phase, lemma: Lemma) -> bool:
        """Hand `lemma` back once more when its statement has used its
        attempts, so that the captain may split, restate, prove, refute or
        retry it now rather than at the replan (once per statement). True
        if jobs on it should continue."""
        if (
            not self.config.aiprover_early_revision
            or lemma.proved
            or lemma.refutation
            or not self._exhausted(lemma)
            or phase.halted()
            or self.agents.claude_budget_left() < 2
        ):
            return False
        key = (lemma.name, normalize(lemma.statement))
        limit = self.config.max_handbacks
        with phase.lock:
            if (
                key in phase.revised
                or not phase.present(lemma)
                or lemma.name in phase.in_handback
                or any(
                    other.name in phase.in_analysis
                    for other in cdcl.ancestors(lemma, phase.sketch.lemmas)
                )
                or (limit and phase.handbacks >= limit)
            ):
                return False
            phase.revised.add(key)
            phase.in_handback.add(lemma.name)
            phase.handbacks += 1
        logger.info(f"{lemma.name}: attempts used; early revision")
        self._decision(
            "early_revision",
            lemma=lemma.name,
            statement=lemma.statement,
            attempts=lemma.attempts,
        )
        try:
            self._handback(phase, lemma)
            if lemma.refutation:
                phase.refuted.set()
        except AgentRefusal as error:
            logger.warning(
                f"{lemma.name}: early revision refused by the model; "
                f"continuing without it ({str(error)[:200]})"
            )
        finally:
            with phase.lock:
                phase.in_handback.discard(lemma.name)
        return not lemma.proved and not self._exhausted(lemma)

    def _analyse_conflict(self, phase: Phase, lemma: Lemma) -> None:
        """Conflict analysis at the nearest decision above `lemma` with
        enough stuck leaves (cdcl.conflict), once per conflict."""
        if not self.config.cdcl or phase.halted():
            return
        with phase.lock:
            if not phase.present(lemma):
                return
            found = cdcl.conflict(
                lemma,
                phase.sketch.lemmas,
                self.config.cdcl_conflict_leaves,
                self.config.aiprover_handback_after,
                phase.analysed,
            )
            if found is None:
                return
            decision, stuck_leaves = found
            below = cdcl.subtree(decision.name, phase.sketch.lemmas)
            limit = self.config.max_handbacks
            if (
                decision.name in phase.in_analysis
                or any(other.name in phase.in_handback for other in below)
                or (limit and phase.handbacks >= limit)
                or self.agents.claude_budget_left() < 2
            ):
                return
            phase.analysed.add(cdcl.conflict_key(decision, stuck_leaves))
            phase.in_analysis.add(decision.name)
            phase.handbacks += 1
        try:
            self._conflict(phase, decision, stuck_leaves)
        except AgentRefusal as error:
            logger.warning(
                f"{decision.name}: conflict analysis refused by the model; "
                f"continuing without it ({str(error)[:200]})"
            )
        finally:
            with phase.lock:
                phase.in_analysis.discard(decision.name)

    def _watch_conflicts(self, phase: Phase) -> None:
        """Look for conflicts over the whole tree every interval. Lemma
        threads check before each job, but with a concurrency limit a stuck
        leaf may wait hours for a thread; analysis must not."""
        handback_after = self.config.aiprover_handback_after
        while self.config.cdcl and not phase.finished.wait(REBALANCE_SECONDS):
            if phase.halted():
                return
            with phase.lock:
                open_leaves = [
                    other
                    for other in phase.sketch.lemmas
                    if cdcl.stuck(other, handback_after)
                ]
            for leaf in open_leaves:
                if phase.finished.is_set() or phase.stopped.is_set():
                    return
                try:
                    self._analyse_conflict(phase, leaf)
                except Exception as error:  # noqa: BLE001
                    # A failed captain call loses this analysis, not the run;
                    # the leaves' own threads still report failures.
                    logger.warning(
                        f"conflict analysis near {leaf.name} failed: "
                        f"{str(error)[:300]}"
                    )

    def _retract(self, phase: Phase, decision: Lemma) -> list[Lemma]:
        """Backjump to `decision` (call with `phase.lock` held): remove its
        subtree, keep the proved lemmas for reuse, and cancel the removed
        lemmas' running jobs."""
        removed = cdcl.retract(decision, phase.sketch.lemmas)
        for other in removed:
            phase.retracted_names.add(other.name)
            if other.proved:
                phase.retracted[normalize(other.statement)] = other
            job = phase.running_jobs.get(other.name)
            if job:
                threading.Thread(
                    target=phase.solver.cancel, args=(job,), daemon=True
                ).start()
        return removed

    def _reuse(self, phase: Phase, lemma: Lemma) -> bool:
        """Prove `lemma` with the proof of a retracted lemma of the same
        statement, if the lemmas that proof uses are present."""
        with phase.lock:
            previous = phase.retracted.get(normalize(lemma.statement))
            names = {other.name for other in phase.sketch.lemmas}
            if previous is None or missing_lemmas(
                previous, names | phase.retracted_names, names
            ):
                return False
            lemma.helpers, lemma.proof = previous.helpers, previous.proof
            lemma.proved = True
        logger.info(f"{lemma.name}: proof reused from {previous.name}")
        self._decision("lemma_reused", lemma=lemma.name)
        return True

    # Jobs and the Lean gate ------------------------------------------------

    def _directed_proof(
        self, phase: Phase, lemma: Lemma, context: str, key: tuple
    ) -> str:
        """The informal proof a job on `lemma` receives: the direction for
        its statement (`aiprover_direction`), fixed before the statement's
        first job, ahead of the source's proof. Without a direction (none
        configured, no comments, no Claude budget left, or a failed call)
        the source's proof alone."""
        source = self.row["informal_proof"]
        mode = self.config.aiprover_direction
        if not mode:
            return source
        with phase.lock:
            direction = phase.directions.get(key)
        if direction is None:
            direction = (
                self._sketch_note(lemma)
                if mode == "sketch"
                else self._captain_direction(lemma, context, source)
            )
            if direction is None:
                return source
            with phase.lock:
                phase.directions[key] = direction
            self._decision(
                "lemma_direction",
                lemma=lemma.name,
                statement=lemma.statement,
                direction=direction,
                source=mode,
            )
        if not direction:
            return source
        return prompts.DIRECTED_PROOF.format(
            lemma_name=lemma.name, direction=direction, informal_proof=source
        )

    def _captain_direction(
        self, lemma: Lemma, context: str, source: str
    ) -> str | None:
        """The captain's informal proof of `lemma`; None without budget or
        after a failed call."""
        if self.agents.claude_budget_left() < 1:
            return None
        prompt = prompts.DIRECTION_TEMPLATE.format(
            lemma_name=lemma.name,
            theorem_text=self.row["informal_statement"],
            context=context.rstrip(),
            statement=lemma.statement,
            informal_proof=source,
        )
        try:
            return self._captain(
                prompt, phase=f"direction/{lemma.name}"
            ).strip()
        except AgentCallError as error:
            logger.warning(f"{lemma.name}: no direction: {error}")
            return None

    def _cited_lemmas(self, earlier: list[Lemma], proof_text: str) -> str:
        """The earlier lemmas a job's direction names, with their
        statements, as facts to cite; empty without any."""
        if not self.config.aiprover_cite_lemmas:
            return ""
        cited = [
            other
            for other in earlier
            if re.search(rf"(?<![\w.']){re.escape(other.name)}(?![\w.'])",
                         proof_text)
        ]
        if not cited:
            return ""
        return prompts.CITED_LEMMAS.format(
            statements="\n\n".join(other.statement for other in cited)
        )

    def _sketch_note(self, lemma: Lemma) -> str:
        """The comment lines directly above `lemma` in the latest captain
        reply that stated it (a sketch, a replan or a split), without the
        comment markers; empty if there are none."""
        pattern = re.compile(
            r"((?:^[ \t]*--.*\n)+)[ \t]*(?:theorem|lemma)\s+"
            + re.escape(lemma.name)
            + r"(?![\w.'])",
            re.M,
        )
        for step in reversed(self.trace.document["steps"]):
            if step["kind"] != "model_call" or step.get("role") != "captain":
                continue
            match = pattern.search(step.get("response") or "")
            if match:
                return "\n".join(
                    line.strip().removeprefix("--").strip()
                    for line in match.group(1).splitlines()
                )
        return ""

    def _one_job(self, phase: Phase, lemma: Lemma, samples: int) -> None:
        """One AIProver job on `lemma`; the first sample that passes the Lean
        gate proves it. The job receives the definitions and earlier lemmas
        as fixed context and the lemma as fixed statement."""
        solver = phase.solver
        with phase.lock:
            lemmas = phase.sketch.lemmas
            earlier = (
                cdcl.context_lemmas(lemma, lemmas)
                if self.config.aiprover_relevant_context
                else lemmas[: lemmas.index(lemma)]
            )
            stubs = stub_listing(earlier)
            known = {other.name for other in lemmas}
            mathlib = phase.mathlib.text(known | phase.run_names)
        if stubs and self.config.aiprover_cite_lemmas:
            stubs = prompts.EARLIER_LEMMAS_HEADER + stubs
        context = self._standalone(phase.form, stubs).rstrip() + "\n"
        fixed_names = {name for _, name, _ in split_declarations(context)}
        key = (lemma.name, normalize(lemma.statement))
        # A job of this statement cut off by a lost server or an
        # interruption continues its sessions instead of starting new ones.
        with phase.lock:
            resume_job = self.state.resumable_jobs.pop(key, None)
        logger.info(
            f"AIProver job on {lemma.name}: "
            + (
                f"resuming {resume_job}"
                if resume_job
                else f"{samples} session(s)"
            )
            + f", attempt {lemma.attempts + 1}/{self._attempts_allowed(0)}"
        )
        errors: list[str] = []
        gated: set[int] = set()

        def gate(sample: AIProverSample) -> bool:
            """Gate a sample once."""
            gated.add(sample.index)
            return self._gate_sample(
                phase, lemma, sample, stubs, fixed_names, errors
            )

        def started(job_id: str) -> None:
            with phase.lock:
                phase.running_jobs[lemma.name] = job_id
            self._decision(
                "aiprover_job_started",
                lemma=lemma.name,
                statement=key[1],
                aiprover_job=job_id,
            )

        job = solver.solve(
            name=f"{self.run_id}_{lemma.name}"[:60],
            theorem_text=prompts.AIPROVER_LEMMA_TEMPLATE.format(
                lemma_name=lemma.name,
                informal_statement=self.row["informal_statement"],
            ),
            proof_text=(proof_text := self._directed_proof(
                phase, lemma, context, key
            )),
            context=context,
            statement=f"{lemma.statement} := by\n  sorry\n",
            samples=samples,
            work_dir=self.temp_dir / "aiprover",
            resume_job=resume_job,
            hint=self._cited_lemmas(earlier, proof_text)
            + knowledge.hint(
                lemma.knowledge,
                mathlib,
                setting=knowledge.setting_index(context),
                clauses=(
                    self.clauses.text([lemma], lemmas)
                    if self.config.cdcl
                    else ""
                ),
            ),
            on_start=started,
            # A session's answer is gated when the session ends; the first
            # accepted one ends the job's other sessions.
            accept=gate,
            # A running session's latest file is gated as well, so a proof
            # written early is not held until the session's time limit. Its
            # errors are not kept: the file is unfinished.
            probe=lambda sample: self._gate_sample(
                phase, lemma, sample, stubs, fixed_names, []
            ),
            probe_seconds=self.config.aiprover_probe_seconds,
        )
        with phase.lock:
            for sample in job.samples:
                phase.mathlib.add(sample.session, sample.lean)
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
            samples=[sample_record(sample) for sample in job.samples],
        )
        if job.error:
            errors.insert(0, job.error)
        if lemma.proved or any(
            gate(sample) for sample in ranked if sample.index not in gated
        ):
            return
        with phase.lock:
            if not phase.present(lemma):
                # Retracted by a backjump while the job ran, and its job
                # cancelled: not a failed attempt.
                return
        # A lost model server is an infrastructure failure, not a failed
        # lemma: the run stops and resumes this lemma instead of replanning.
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
        self._record_failure(phase, lemma, job, errors, fixed_names)

    def _record_failure(
        self, phase: Phase, lemma: Lemma, job, errors, fixed_names
    ) -> None:
        """Count a job that did not prove `lemma`, and keep its errors, the
        samples' own code (most complete first) and their notes for the
        next jobs, the captain and the replan."""
        with phase.lock:
            self.state.count_attempt(lemma)
        lemma.last_errors = "\n\n".join(errors)[:3000]
        codes = [
            failed_attempt(sample, lemma.name, fixed_names)
            for sample in job.samples
        ]
        attempts = sorted(filter(None, codes), key=lambda entry: entry[0])
        lemma.last_attempts = "\n\n".join(
            text[:MAX_ATTEMPT_CHARS]
            for _, text in attempts[:MAX_ATTEMPTS_KEPT]
        )
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
            f"(attempt {lemma.attempts}): "
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

    def _gate_sample(
        self,
        phase: Phase,
        lemma: Lemma,
        sample: AIProverSample,
        stubs: str,
        fixed_names: set[str],
        errors: list[str],
    ) -> bool:
        """Prove `lemma` from one sample; on failure append to `errors`.

        The proof is spliced under the sketch's statement, then, if that
        fails, the answer's whole theorem is kept as a helper. Helpers are
        renamed into the lemma's namespace of names (`<lemma>_`); the
        answer's own names are tried as well if the renaming broke a
        reference.
        """
        label = f"sample {sample.index} ({sample.status})"
        extracted = extract_lemma_proof(sample.lean, lemma.name, fixed_names)
        if extracted is None:
            errors.append(f"{label}: no proof of `{lemma.name}` in the answer")
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
            found = forbidden_problems(helpers + "\n" + proof)
            if found:
                problems += found
                continue
            renamed_helpers, renamed_proof, renamed = prefixed_helpers(
                helpers, proof, f"{lemma.name}_"
            )
            problem = self._prove_from(
                phase,
                lemma,
                sample,
                stubs,
                renamed_helpers,
                renamed_proof,
                alias + suffix,
            )
            if problem and any(name in problem for name in renamed):
                problem = self._prove_from(
                    phase,
                    lemma,
                    sample,
                    stubs,
                    helpers,
                    proof,
                    alias + suffix + "_names",
                )
            if not problem:
                return True
            problems.append(problem)
        errors.append(f"{label}: " + "\n".join(problems))
        return False

    def _prove_from(
        self,
        phase: Phase,
        lemma: Lemma,
        sample: AIProverSample,
        stubs: str,
        helpers: str,
        proof: str,
        label: str,
    ) -> str:
        """Check one candidate proof of `lemma` from a sample and record it
        if it holds; empty on success, else the problem."""
        problem = self._helper_clash(phase, lemma, helpers)
        if problem:
            return problem
        _, problem = self._check_proof(
            phase.form,
            stubs,
            lemma,
            helpers,
            proof,
            label,
            worker=sample.index,
            round=0,
        )
        if problem:
            return problem
        # Checked again under the lock: another lemma may have been proved
        # with the same helper names, or this one retracted, while Lean ran.
        problem = self._helper_clash(phase, lemma, helpers)
        with phase.lock:
            if not phase.present(lemma):
                return "the lemma was retracted by a backjump"
            if problem or lemma.proved:
                return problem
            lemma.helpers, lemma.proof = helpers, proof
            lemma.proved = True
        kept = ", own statement kept" if "_kept" in label else ""
        # A session that did not end verified may still hold a proof of the
        # lemma, which Lean has just accepted on its own.
        source = (
            "verified"
            if sample.status == "verified"
            else "session running; lemma proof checked"
            if sample.status == "running"
            else f"session ended {sample.status}; lemma proof checked"
        )
        logger.info(
            f"lemma {lemma.name} proved by AIProver sample {sample.index} "
            f"({source}{kept})"
        )
        self._record_proof(lemma, sample.index)
        return ""

    def _helper_clash(self, phase: Phase, lemma: Lemma, helpers: str) -> str:
        """The helpers of `lemma` that reuse a name of the sketch's lemmas
        or of the other proved lemmas' helpers, which share the assembled
        file; empty if none does."""
        with phase.lock:
            taken = {
                other.name for other in phase.sketch.lemmas
            } | proved_helper_names(phase.sketch.lemmas, lemma)
            names = sorted(helper_names(helpers) & taken)
        if not names:
            return ""
        return (
            f"helpers {names} collide with sketch lemmas or the helpers of "
            f"other lemmas"
        )
