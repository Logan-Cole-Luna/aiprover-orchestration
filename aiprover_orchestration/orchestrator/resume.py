"""Reconstruction of a run's progress from its trace, for resuming.

The trace records every completed step: compiled formalizations, audit
verdicts, accepted sketches, proved lemmas and replans. Replaying these
decisions yields the latest durable state, so an interrupted run continues
from its last completed step instead of from the beginning.
"""

from dataclasses import dataclass, field

from . import prompts
from ..lean.text import file_scoped, normalize
from .structures import (
    Formalization,
    Sketch,
    missing_lemmas,
    parse_lemmas,
    parse_statement,
)


@dataclass
class ResumeState:
    formalization: Formalization | None = None
    audited: bool = False  # the latest formalization has a verdict
    audit_rounds_done: int = 0
    formalize_feedback: str = ""  # set when the latest verdict is REVISE
    sketch: Sketch | None = None
    replans_started: int = 0
    replan_pending: bool = (
        False  # a replan began but no new sketch was accepted
    )
    lean_checks_done: int = 0
    previous_status: str | None = None
    restored_steps: int = 0
    proofs: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Last failed attempts per lemma of the current sketch: (errors, attempts).
    failures: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Completed AIProver jobs without a proof, per (lemma name, statement).
    attempts: dict[tuple[str, str], int] = field(default_factory=dict)
    # (lemma name, statement) pairs already handed back to the captain.
    handed_back: set[tuple[str, str]] = field(default_factory=set)
    # Attempts per (lemma name, statement) at the captain's last retry.
    reviewed_at: dict[tuple[str, str], int] = field(default_factory=dict)
    # Knowledge carried per lemma (knowledge.py).
    knowledge: dict[str, str] = field(default_factory=dict)
    # AIProver job per (lemma name, statement) that started but did not
    # complete (lost model server, interrupted run): its sessions are resumed
    # rather than restarted.
    resumable_jobs: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def faithful(self) -> bool:
        return (
            self.formalization is not None
            and self.audited
            and self.formalization.verdict.startswith("FAITHFUL")
        )

    def describe(self) -> dict:
        return {
            "restored_steps": self.restored_steps,
            "formalization": (
                None
                if self.formalization is None
                else (
                    "faithful"
                    if self.faithful
                    else (
                        "audited, revise"
                        if self.audited
                        else "compiled, not audited"
                    )
                )
            ),
            "audit_rounds_done": self.audit_rounds_done,
            "sketch_lemmas": (
                None
                if self.sketch is None
                else [lemma.name for lemma in self.sketch.lemmas]
            ),
            "lemmas_proved": (
                []
                if self.sketch is None
                else [
                    lemma.name for lemma in self.sketch.lemmas if lemma.proved
                ]
            ),
            "replans_started": self.replans_started,
            "replan_pending": self.replan_pending,
            "previous_status": self.previous_status,
        }


def restore_state(document: dict) -> ResumeState:
    """Replay the decision steps of a trace document into a ResumeState."""
    state = ResumeState(
        restored_steps=len(document.get("steps", [])),
        previous_status=(document.get("outcome") or {}).get("status"),
    )
    statement_of: dict[str, str] = {}  # lemma name -> statement, current sketch
    names_seen: set[str] = set()  # lemma names of every sketch and split
    last_job: dict[str, dict] = {}  # lemma name -> its latest AIProver job
    for step in document.get("steps", []):
        if step["kind"] == "model_call" and step.get("backend") == "aiprover":
            last_job[step.get("lemma")] = step
            # Sessions cut off by a lost server or a cancellation can be resumed
            # until the job is counted as an attempt or its lemma is proved.
            samples = step.get("samples") or []
            if (
                step.get("aiprover_job")
                and step.get("lemma") in statement_of
                and any(
                    sample.get("status") in ("infra", "cancelled")
                    for sample in samples
                )
            ):
                key = (step["lemma"], normalize(statement_of[step["lemma"]]))
                state.resumable_jobs[key] = step["aiprover_job"]
        if step["kind"] == "lean_check":
            state.lean_checks_done += 1
            continue
        if step["kind"] != "decision":
            continue
        event = step["event"]
        # An AIProver job is resumable from its start until it completes (its
        # lemma proved or the attempt counted).
        if event == "aiprover_job_started":
            state.resumable_jobs[(step["lemma"], step["statement"])] = step[
                "aiprover_job"
            ]
        elif event == "solver_budget_exhausted" and step.get("aiprover_job"):
            state.resumable_jobs = {
                key: job
                for key, job in state.resumable_jobs.items()
                if job != step["aiprover_job"]
            }
        elif event == "lemma_proved":
            state.resumable_jobs = {
                key: job
                for key, job in state.resumable_jobs.items()
                if key[0] != step["lemma"]
            }
        if event == "formalization_compiled":
            preamble, name, signature = parse_statement(step["statement"])
            state.formalization = Formalization(
                step["definitions"],
                file_scoped(step["preamble"]),
                name,
                signature,
                notes=step.get("notes", ""),
            )
            state.audited = False
            state.formalize_feedback = ""
        elif event == "audit_verdict" and state.formalization is not None:
            form = state.formalization
            form.verdict, form.issues = step["verdict"], step["issues"]
            form.readback = step["readback"]
            state.audited = True
            state.audit_rounds_done = step["round"] + 1
            if not form.verdict.startswith("FAITHFUL"):
                state.formalize_feedback = (
                    prompts.FORMALIZE_FEEDBACK_TEMPLATE.format(
                        definitions=form.definitions,
                        statement=form.statement,
                        problems="Auditor read-back:\n"
                        + form.readback
                        + "\n\nCaptain review:\n"
                        + form.issues,
                    )
                )
        elif event == "sketch_accepted":
            lemmas = [
                lemma
                for statement in step["lemmas"]
                for lemma in parse_lemmas(statement + " := by sorry")
            ]
            state.sketch = Sketch(lemmas, step["main_proof"])
            statement_of = {lemma.name: lemma.statement for lemma in lemmas}
            names_seen |= set(statement_of)
            state.failures = {}
            state.replan_pending = False
        elif event == "lemma_proved" and step["lemma"] in statement_of:
            state.proofs[normalize(statement_of[step["lemma"]])] = (
                step["helpers"],
                step["proof"],
            )
        elif (
            event == "solver_budget_exhausted" and step["lemma"] in statement_of
        ):
            samples = (last_job.get(step["lemma"]) or {}).get("samples") or []
            stopped = samples and all(
                sample.get("status") in ("cancelled", "infra")
                for sample in samples
            )
            if "aiprover_job" in step and not stopped:
                key = (step["lemma"], normalize(statement_of[step["lemma"]]))
                state.attempts[key] = state.attempts.get(key, 0) + 1
            if step.get("attempts") or step.get("errors"):
                state.failures[step["lemma"]] = (
                    step.get("errors", ""),
                    step.get("attempts", ""),
                )
            if "knowledge" in step:
                state.knowledge[step["lemma"]] = step["knowledge"]
        elif (
            event == "lemma_handback"
            and state.sketch is not None
            and step["lemma"] in statement_of
        ):
            # Apply the captain's change to the sketch as the run did.
            target = next(
                lemma
                for lemma in state.sketch.lemmas
                if lemma.name == step["lemma"]
            )
            if "knowledge" in step:
                state.knowledge[target.name] = step["knowledge"]
            if step["action"] == "restate":
                target.statement = step["statement"]
                statement_of[target.name] = target.statement
                continue
            key = (target.name, normalize(target.statement))
            if step["action"] == "retry" and "attempts" in step:
                state.handed_back.discard(key)
                state.reviewed_at[key] = step["attempts"]
                continue
            state.handed_back.add(key)
            if step["action"] == "split":
                position = state.sketch.lemmas.index(target)
                for offset, statement in enumerate(
                    step.get("new_lemmas") or []
                ):
                    for lemma in parse_lemmas(statement + " := by sorry"):
                        lemma.generation = target.generation + 1
                        state.sketch.lemmas.insert(position + offset, lemma)
                        statement_of[lemma.name] = lemma.statement
                        names_seen.add(lemma.name)
        elif event == "replan":
            state.replans_started += 1
            state.replan_pending = True
    if state.sketch is not None:
        present = {lemma.name for lemma in state.sketch.lemmas}
        for lemma in state.sketch.lemmas:
            key = (lemma.name, normalize(lemma.statement))
            lemma.attempts = state.attempts.get(key, 0)
            lemma.handed_back = key in state.handed_back
            lemma.reviewed_at = state.reviewed_at.get(key, 0)
            lemma.knowledge = state.knowledge.get(lemma.name, "")
            proof = state.proofs.get(normalize(lemma.statement))
            if proof:
                lemma.helpers, lemma.proof = proof
                # A proof kept across a replan needs the lemmas it used.
                lemma.proved = not missing_lemmas(lemma, names_seen, present)
                if not lemma.proved:
                    lemma.helpers = lemma.proof = ""
            elif lemma.name in state.failures:
                lemma.last_errors, lemma.last_attempts = state.failures[
                    lemma.name
                ]
    return state
