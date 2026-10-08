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

from pathlib import Path

from ... import roles

PROMPT_DIR = Path(__file__).parent
LEAN_ENVIRONMENT_MARKER = "{lean_environment}"


def template(name: str) -> str:
    return (PROMPT_DIR / "templates" / f"{name}.md").read_text()


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

# Sketch, replan and hand-back.
SKETCH_TEMPLATE = template("sketch")
SKETCH_REPAIR_TEMPLATE = template("sketch_repair")
REPLAN_FEEDBACK_TEMPLATE = template("replan_feedback")
HANDBACK_TEMPLATE = template("handback")
HANDBACK_REPAIR_TEMPLATE = template("handback_repair")

# Solvers.
SOLVER_TEMPLATE = template("solver")
SOLVER_REPAIR_TEMPLATE = template("solver_repair")
AIPROVER_LEMMA_TEMPLATE = template("aiprover_lemma")

# Review, report and informalization.
FINAL_REVIEW_TEMPLATE = template("final_review")
REPORT_TEMPLATE = template("report")
REPORT_REPAIR_TEMPLATE = template("report_repair")
REPORT_CHECK_TEMPLATE = template("report_check")
REPORT_REVISE_TEMPLATE = template("report_revise")
INFORMAL_TEMPLATE = template("informal")
INFORMAL_CHECK_TEMPLATE = template("informal_check")

TEMPLATES = {
    name: value
    for name, value in globals().items()
    if name.endswith("_TEMPLATE")
}
