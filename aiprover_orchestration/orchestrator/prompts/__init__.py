"""Prompts of the orchestration roles.

System prompts (`system/`) set each role's instructions; the marker
`{lean_environment}` in them is replaced by `system/lean_environment.md`.
Templates (`templates/`) are filled with `str.format`, so literal braces in
them are doubled. The auditor's system prompt is its role guide
(`aiprover_orchestration/roles/auditor.md`), used verbatim.

Captain: formalizes, judges read-backs, writes and revises proof sketches.
Auditor: writes blind read-backs. Solver: proves one lemma under the
library's gating rules. Reviewer: referees the verified result. Writer:
writes the report and the informalization.
"""

import re
from pathlib import Path

from ... import roles

PROMPT_DIR = Path(__file__).parent
LEAN_ENVIRONMENT_MARKER = "{lean_environment}"


# HTML comments in templates are removed on loading: they hold disabled
# instructions (conflict-driven learning, cdcl.py) and notes.
COMMENT = re.compile(r"\n?<!--.*?-->\n?", re.S)


def template(name: str) -> str:
    text = (PROMPT_DIR / "templates" / f"{name}.md").read_text()
    return COMMENT.sub("", text)


def system_prompt(role: str) -> str:
    text = (PROMPT_DIR / "system" / f"{role}.md").read_text()
    return text.replace(LEAN_ENVIRONMENT_MARKER, LEAN_ENV_NOTE)


def auditor_system() -> str:
    """The auditor role guide, used verbatim as the system prompt."""
    return roles.load("auditor")


LEAN_ENV_NOTE = (PROMPT_DIR / "system" / "lean_environment.md").read_text()

CAPTAIN_SYSTEM = system_prompt("captain")
SOLVER_SYSTEM = system_prompt("solver")
REVIEWER_SYSTEM = system_prompt("reviewer")
WRITER_SYSTEM = system_prompt("writer")

# Formalization and audit.
FORMALIZE_TEMPLATE = template("formalize")
FORMALIZE_LIBRARY_TEMPLATE = template("formalize_library")
FORMALIZE_FEEDBACK_TEMPLATE = template("formalize_feedback")
AUDITOR_TEMPLATE = template("auditor")
JUDGE_READBACK_TEMPLATE = template("judge_readback")

# Setting check, sketch, replan and hand-back.
SETTING_CHECK_TEMPLATE = template("setting_check")
SETTING_CHECK_REPAIR_TEMPLATE = template("setting_check_repair")
SKETCH_TEMPLATE = template("sketch")
SKETCH_REPAIR_TEMPLATE = template("sketch_repair")
SKETCH_JUDGE_TEMPLATE = template("sketch_judge")
REFUTATION_REPAIR_TEMPLATE = template("refutation_repair")
REPLAN_FEEDBACK_TEMPLATE = template("replan_feedback")
HANDBACK_TEMPLATE = template("handback")
HANDBACK_REPAIR_TEMPLATE = template("handback_repair")
# Conflict analysis over the lemma tree (cdcl.py).
CONFLICT_TEMPLATE = template("conflict")
# Learned clauses as prompts receive them, when the run learns clauses.
CLAUSES_BLOCK = (
    "\n<learned_clauses>\n{clauses}\n</learned_clauses>\n"
    "Learned clauses hold for the whole run (refuted statements, routes that "
    "failed and why, pitfalls of the encoding): do not state, propose or "
    "retry what they exclude.\n"
)
CLAUSE_OUTPUT = (
    "Record what this taught as learned clauses, one per <clause>fact"
    "</clause> tag: a single fact of at most two sentences that holds beyond "
    "these lemmas and that later sessions and decisions should not "
    "rediscover, such as a declaration absent from Mathlib, the Mathlib "
    "lemma that does a step, or why a route fails. A clause never holds a "
    "proof.\n"
)
BACKJUMP_ACTION = (
    "- Backjump, when the cause lies in the decision: <backjump>theorem "
    "{decision_name} <binders> : <type> := by sorry</backjump>, the decision "
    "restated so that the cause is removed (for example in standard Mathlib "
    "vocabulary). All lemmas below `{decision_name}` are retracted; "
    "`{decision_name}` is reopened with the new statement and handed to "
    "solvers, and proofs of retracted lemmas are reused if their statements "
    "return. The main proof and the proofs that use `{decision_name}` must "
    "still compile with the new statement.\n"
)
HANDBACK_SOLVERS = (
    "Solver agents have failed {attempts} times to prove the lemma "
    "`{lemma_name}` of your proof sketch. The other lemmas are still being "
    "proved."
)
HANDBACK_ASSEMBLY = (
    "The final assembly of your proof failed verification because of the "
    "lemma `{lemma_name}`, whose accepted proof is withdrawn: {problem} "
    "Its withdrawn proof is under <last_attempts> below; every other lemma "
    "stays proved. Diagnose the failure and choose one action; for a "
    "failure confined to a step of the proof, prefer to correct the proof."
)
REPLAN_ASSEMBLY = (
    "\n\nAll lemmas were proved, but the assembled proof failed "
    "verification for a reason in the sketch itself:\n{failure}\n"
)
NO_RETRY = (
    "Retry is not available: the solvers have failed on this statement "
    "after {retries} retries. Prove it yourself, split it, restate it or "
    "refute it.\n\n"
)
# After a hand-back reply that stopped at its spend or turn limit.
NO_DIRECT_PROOF = (
    "Your previous reply stopped at the hand-back's spend or turn limit, so "
    "proving `{lemma_name}` yourself (action 1) is not available. Split it "
    "into lemmas the solvers can prove, with a short proof of `{lemma_name}` "
    "from them, or restate, retry or refute it.\n\n"
)
NO_BACKJUMP = (
    "Backjumping is off in this run: name a shared cause in the diagnosis "
    "and the clauses, and choose local.\n"
)

# Solvers.
SOLVER_TEMPLATE = template("solver")
SOLVER_REPAIR_TEMPLATE = template("solver_repair")
AIPROVER_LEMMA_TEMPLATE = template("aiprover_lemma")
DIRECTION_TEMPLATE = template("direction")
# Above the earlier lemmas in an AIProver job's fixed context.
EARLIER_LEMMAS_HEADER = (
    "-- Earlier lemmas of this proof. Each is proved elsewhere in the run;\n"
    "-- its `sorry` stands for that proof. They are facts to cite by name,\n"
    "-- not goals: do not prove, restate or re-derive them.\n\n"
)
# Ahead of the guidance of a job whose direction names earlier lemmas.
CITED_LEMMAS = (
    "Lemmas to cite (proved elsewhere in the run; apply them by name, do "
    "not prove them again):\n```lean\n{statements}\n```\n\n"
)
# The informal proof an AIProver job receives with a captain's direction.
DIRECTED_PROOF = (
    "Proof of the lemma `{lemma_name}` (the captain's direction):\n"
    "{direction}\n\n"
    "The source's proof of the main result, for context:\n{informal_proof}"
)
# The same without the source's proof (`aiprover_condensed_prompt`).
DIRECTION_ONLY = (
    "Proof of the lemma `{lemma_name}` (the captain's direction):\n"
    "{direction}\n"
)

# Review, report and informalization.
FINAL_REVIEW_TEMPLATE = template("final_review")
REPORT_TEMPLATE = template("report")
REPORT_REPAIR_TEMPLATE = template("report_repair")
REPORT_CHECK_TEMPLATE = template("report_check")
REPORT_REVISE_TEMPLATE = template("report_revise")
INFORMAL_TEMPLATE = template("informal")
INFORMAL_REFUTATION_TEMPLATE = template("informal_refutation")
INFORMAL_CHECK_TEMPLATE = template("informal_check")
PAPER_TEMPLATE = template("paper")
PAPER_CHECK_TEMPLATE = template("paper_check")

TEMPLATES = {
    name: value
    for name, value in globals().items()
    if name.endswith("_TEMPLATE")
}
