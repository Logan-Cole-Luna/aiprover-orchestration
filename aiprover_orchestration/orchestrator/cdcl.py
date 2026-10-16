"""Conflict-driven learning over the lemma tree, after CDCL SAT solvers.

A conflict-driven clause-learning (CDCL) solver assigns variables, and when an
assignment leads to a conflict it analyses the conflict, learns a clause that
excludes its cause, and backjumps to the decision level of that cause rather
than undoing only the last decision. The orchestration searches a tree of
decisions as well, and the same three mechanisms apply:

    CDCL                   orchestration
    decision               a sketch lemma, a split, the encoding of a lemma
    unit propagation       closing easy leaves cheaply (the captain with Lean
                           tools) before spending AIProver sessions
    conflict               several leaves under one decision stuck, or a leaf
                           refuted
    conflict analysis      the captain names the shared cause (`analyse`)
    learned clause         a run-wide fact every later job and prompt
                           receives (`LearnedClause`, `ClauseStore`)
    backjump               retract the subtree under the decision that caused
                           the conflict and restate that decision
                           (`retract`), keeping proved lemmas for reuse
    restart                a replan keeping proved lemmas and the clauses

Learned clauses are recorded as `clause_learned` decisions and backjumps as
`backjump` decisions, so both survive a resume (`ClauseStore.from_steps`,
`resume.restore_state`).

Related published work:
- GRASP (Marques-Silva and Sakallah, IEEE Trans. Computers 1999): conflict
  analysis, learned clauses, non-chronological backtracking.
- Chaff (Moskewicz et al., DAC 2001): VSIDS activity, first-UIP learning.
- MiniSat (Eén and Sörensson, SAT 2003).
- MAESTRO (docs/papers/34453_Maestro_Agentic_Formal_P.pdf), §3.1 to 3.3:
  plan-level failure causes and branch retraction for proof agents.
"""

import re
import threading
from dataclasses import asdict, dataclass

from ..lean.text import normalize
from .structures import Lemma

# Kinds of learned clause:
#   refuted        a statement whose negation compiled; never state it again
#   failed_route   an approach tried for a statement and why it failed
#   conflict_cause the decision a backjump retracted, and the shared cause
#   fact           any other fact the captain learned (an absent Mathlib
#                  declaration, a working lemma, a pitfall of the encoding)
CLAUSE_KINDS = ("refuted", "failed_route", "conflict_cause", "fact")
MAX_CLAUSES_CHARS = 4000
MAX_CLAUSE_CHARS = 400
CLAUSES_HEADER = (
    "Learned clauses: facts this run established after conflicts (failed or "
    "refuted lemmas). They hold for every lemma; do not retry what they "
    "exclude."
)


@dataclass(frozen=True)
class LearnedClause:
    kind: str
    subject: str  # the statement, decision or name the clause is about
    fact: str
    lemma: str = ""  # the lemma whose conflict taught it

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, normalize(self.subject), normalize(self.fact))

    def line(self) -> str:
        subject = " ".join(self.subject.split())[:160]
        fact = " ".join(self.fact.split())[:MAX_CLAUSE_CHARS]
        if not subject:
            return f"- [{self.kind}] {fact}"
        return f"- [{self.kind}] {subject}: {fact}"


class ClauseStore:
    """The clauses a run has learned, shared by every job and prompt."""

    def __init__(self) -> None:
        self._clauses: dict[tuple, LearnedClause] = {}
        self._lock = threading.Lock()

    def add(self, clause: LearnedClause) -> bool:
        """Learn `clause`; False if it was known."""
        with self._lock:
            if clause.key in self._clauses:
                return False
            self._clauses[clause.key] = clause
            return True

    def clauses(self) -> list[LearnedClause]:
        with self._lock:
            return list(self._clauses.values())

    def refuted_statements(self) -> dict[str, str]:
        """Normalized statement to lemma name, for every refuted clause."""
        return {
            normalize(clause.subject): clause.lemma
            for clause in self.clauses()
            if clause.kind == "refuted"
        }

    def text(self) -> str:
        """The clauses as guidance, newest first, within MAX_CLAUSES_CHARS."""
        lines, size = [], len(CLAUSES_HEADER)
        for clause in reversed(self.clauses()):
            line = clause.line()
            if size + len(line) > MAX_CLAUSES_CHARS:
                break
            lines.append(line)
            size += len(line) + 1
        return f"{CLAUSES_HEADER}\n" + "\n".join(lines) if lines else ""

    @classmethod
    def from_steps(cls, steps: list[dict]) -> "ClauseStore":
        """The clauses recorded in a trace: `clause_learned` decisions, and
        refutations of lemmas from hand-backs recorded before clauses
        existed."""
        store = cls()
        statement_of: dict[str, str] = {}
        for step in steps:
            if step.get("kind") != "decision":
                continue
            event = step.get("event")
            if event == "sketch_accepted":
                for statement in step.get("lemmas") or []:
                    match = re.match(r"\s*theorem\s+([^\s(:{\[]+)", statement)
                    if match:
                        statement_of[match.group(1)] = statement
            elif event == "lemma_handback":
                if step.get("action") == "restate":
                    statement_of[step["lemma"]] = step["statement"]
                elif step.get("action") == "refute":
                    store.add(
                        LearnedClause(
                            "refuted",
                            statement_of.get(step["lemma"], step["lemma"]),
                            "its negation compiled in Lean",
                            step["lemma"],
                        )
                    )
            elif event == "backjump":
                statement_of[step["lemma"]] = step["statement"]
            elif event == "clause_learned":
                store.add(
                    LearnedClause(
                        step["clause_kind"],
                        step.get("subject", ""),
                        step["fact"],
                        step.get("lemma", ""),
                    )
                )
        return store


def clause_fields(clause: LearnedClause) -> dict:
    """The fields of a `clause_learned` decision."""
    fields = asdict(clause)
    fields["clause_kind"] = fields.pop("kind")
    return fields


# ── The lemma tree ─────────────────────────────────────────────────────────


def children(name: str, lemmas: list[Lemma]) -> list[Lemma]:
    return [lemma for lemma in lemmas if lemma.parent == name]


def subtree(name: str, lemmas: list[Lemma]) -> list[Lemma]:
    """The descendants of `name` (through splits), depth first."""
    found = []
    for child in children(name, lemmas):
        found.append(child)
        found += subtree(child.name, lemmas)
    return found


def ancestors(lemma: Lemma, lemmas: list[Lemma]) -> list[Lemma]:
    """The decisions above `lemma`, nearest first."""
    by_name = {other.name: other for other in lemmas}
    chain, current = [], by_name.get(lemma.parent)
    while current is not None and current not in chain:
        chain.append(current)
        current = by_name.get(current.parent)
    return chain


def stuck(lemma: Lemma, handback_after: int) -> bool:
    """An unproved lemma the solvers and the captain have failed on: refuted,
    handed back without success, or past two hand-back intervals."""
    return not lemma.proved and (
        bool(lemma.refutation)
        or lemma.handed_back
        or lemma.attempts >= 2 * max(1, handback_after)
    )


def referenced_outside(
    ancestor: Lemma, lemmas: list[Lemma], retracted: set[str]
) -> bool:
    """True if a proved lemma outside the retracted subtree uses `ancestor`:
    a backjump would invalidate that proof, so the conflict is analysed at a
    higher decision instead."""
    pattern = rf"(?<![\w.']){re.escape(ancestor.name)}(?![\w'])"
    return any(
        re.search(pattern, other.helpers + "\n" + other.proof)
        for other in lemmas
        if other.proved
        and other is not ancestor
        and other.name not in retracted
    )


def conflict(
    lemma: Lemma,
    lemmas: list[Lemma],
    threshold: int,
    handback_after: int,
    analysed: set[tuple],
) -> tuple[Lemma, list[Lemma]] | None:
    """The decision to analyse for a conflict around `lemma`, and its stuck
    leaves: the nearest ancestor with at least `threshold` stuck descendants
    or a refuted one, that no proof outside its subtree uses and that was not
    analysed with the same stuck leaves (`analysed`)."""
    for ancestor in ancestors(lemma, lemmas):
        below = subtree(ancestor.name, lemmas)
        stuck_leaves = [
            other for other in below if stuck(other, handback_after)
        ]
        if len(stuck_leaves) < threshold and not any(
            other.refutation for other in stuck_leaves
        ):
            continue
        retracted = {other.name for other in below}
        if referenced_outside(ancestor, lemmas, retracted):
            continue
        key = conflict_key(ancestor, stuck_leaves)
        if key in analysed:
            return None
        return ancestor, stuck_leaves
    return None


def conflict_key(ancestor: Lemma, stuck_leaves: list[Lemma]) -> tuple:
    return (
        ancestor.name,
        normalize(ancestor.statement),
        frozenset(other.name for other in stuck_leaves),
    )


def retract(ancestor: Lemma, lemmas: list[Lemma]) -> list[Lemma]:
    """Backjump: remove the subtree under `ancestor` from `lemmas` (in place)
    and reopen `ancestor`; the removed lemmas are returned, so that their
    proofs can be reused if their statements return."""
    removed = subtree(ancestor.name, lemmas)
    names = {other.name for other in removed}
    lemmas[:] = [other for other in lemmas if other.name not in names]
    ancestor.proved = False
    ancestor.helpers = ancestor.proof = ""
    ancestor.attempts = ancestor.reviewed_at = 0
    ancestor.handed_back = False
    ancestor.last_errors = ancestor.last_attempts = ""
    return removed
