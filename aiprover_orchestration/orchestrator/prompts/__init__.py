"""Prompts of the orchestration roles.

System prompts (`system/`) set each role's instructions; the marker
`{lean_environment}` in them is replaced by `system/lean_environment.md`.
Templates (`templates/`) are filled with `str.format`, so literal braces in
them are doubled. The checker's system prompt is its role guide
(`aiprover_orchestration/roles/checker.md`), used verbatim.

Orchestrator: formalizes, judges read-backs, writes and revises proof sketches.
Judge: judges read-backs independently of the orchestrator. Formalizer: writes the
reference formalization. Checker: writes blind read-backs. Prover: proves one lemma under the
library's gating rules. Reviewer: referees the verified result. Writer:
writes the report and the informalization.
"""

import re
from pathlib import Path

from ... import roles
from ...paths import LEAN_RELEASE

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


def checker_system() -> str:
    """The checker role guide, used verbatim as the system prompt."""
    return roles.load("checker")


LEAN_ENV_NOTE = (
    (PROMPT_DIR / "system" / "lean_environment.md")
    .read_text()
    .replace("{lean_release}", LEAN_RELEASE)
)

ORCHESTRATOR_SYSTEM = system_prompt("orchestrator")
PROVER_SYSTEM = system_prompt("prover")
REVIEWER_SYSTEM = system_prompt("reviewer")
JUDGE_SYSTEM = system_prompt("judge")
WRITER_SYSTEM = system_prompt("writer")

# Formalization and audit.
FORMALIZE_TEMPLATE = template("formalize")
FORMALIZE_LIBRARY_TEMPLATE = template("formalize_library")
FORMALIZE_FEEDBACK_TEMPLATE = template("formalize_feedback")
CHECKER_TEMPLATE = template("checker")
JUDGE_READBACK_TEMPLATE = template("judge_readback")

# Vacuity check, sketch, replan and escalation.
VACUITY_CHECK_TEMPLATE = template("vacuity_check")
VACUITY_CHECK_REPAIR_TEMPLATE = template("vacuity_check_repair")
SKETCH_TEMPLATE = template("sketch")
SKETCH_REPAIR_TEMPLATE = template("sketch_repair")
SKETCH_JUDGE_TEMPLATE = template("sketch_judge")
BLUEPRINT_TEMPLATE = template("blueprint")
PLAN_REVISION_TEMPLATE = template("plan_revision")
PLAN_GUIDANCE_TEMPLATE = template("plan_guidance")
PLAN_FINDINGS_TEMPLATE = template("plan_findings")

# Project tasks.
RESEARCH_TEMPLATE = template("research")
RESEARCH_CRITIC_TEMPLATE = template("research_critic")
CLAIM_CHECK_TEMPLATE = template("claim_check")
REFUTATION_REPAIR_TEMPLATE = template("refutation_repair")
REPLAN_FEEDBACK_TEMPLATE = template("replan_feedback")
ESCALATION_TEMPLATE = template("escalation")
ESCALATION_REPAIR_TEMPLATE = template("escalation_repair")
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
    "provers, and proofs of retracted lemmas are reused if their statements "
    "return. The main proof and the proofs that use `{decision_name}` must "
    "still compile with the new statement.\n"
)
ESCALATION_PROVERS = (
    "Prover agents have failed {attempts} times to prove the lemma "
    "`{lemma_name}` of your proof sketch. The other lemmas are still being "
    "proved."
)
ESCALATION_ASSEMBLY = (
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
    "Retry is not available: the provers have failed on this statement "
    "after {retries} retries. Prove it yourself, split it, restate it or "
    "refute it.\n\n"
)
# After an escalation reply that stopped at its spend or turn limit.
NO_DIRECT_PROOF = (
    "Your previous reply stopped at the escalation's spend or turn limit, so "
    "proving `{lemma_name}` yourself (action 1) is not available. Split it "
    "into lemmas the provers can prove, with a short proof of `{lemma_name}` "
    "from them, or restate, retry or refute it.\n\n"
)
NO_BACKJUMP = (
    "Backjumping is off in this run: name a shared cause in the diagnosis "
    "and the clauses, and choose local.\n"
)

# Provers.
PROVER_TEMPLATE = template("prover")
PROVER_REPAIR_TEMPLATE = template("prover_repair")
AIPROVER_LEMMA_TEMPLATE = template("aiprover_lemma")
GUIDANCE_TEMPLATE = template("guidance")
# Above the earlier lemmas in an AIProver job's fixed context.
EARLIER_LEMMAS_HEADER = (
    "-- Earlier lemmas of this proof. Each is proved elsewhere in the run;\n"
    "-- its `sorry` stands for that proof. They are facts to cite by name,\n"
    "-- not goals: do not prove, restate or re-derive them.\n\n"
)
# Ahead of the guidance of a job whose guidance names earlier lemmas.
CITED_LEMMAS = (
    "Lemmas to cite (proved elsewhere in the run; apply them by name, do "
    "not prove them again):\n```lean\n{statements}\n```\n\n"
)
# The informal proof an AIProver job receives with an orchestrator's guidance.
DIRECTED_PROOF = (
    "Proof of the lemma `{lemma_name}` (the orchestrator's guidance):\n"
    "{guidance}\n\n"
    "The source's proof of the main result, for context:\n{informal_proof}"
)
# The same without the source's proof (`aiprover_condensed_prompt`).
DIRECTION_ONLY = (
    "Proof of the lemma `{lemma_name}` (the orchestrator's guidance):\n"
    "{guidance}\n"
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
PROPOSAL_TEMPLATE = template("proposal")
DRAFT_TEMPLATE = template("draft")
REDRAFT_TEMPLATE = template("redraft")
SIDE_TEMPLATE = template("side")
APPROACHES_TEMPLATE = template("approaches")
RANK_APPROACHES_TEMPLATE = template("rank_approaches")
ORCHESTRATOR_PROOFS_TEMPLATE = template("orchestrator_proofs")
GAPS_SKETCH_TEMPLATE = template("gaps_sketch")
GAPS_JUDGE_TEMPLATE = template("gaps_judge")
JUDGE_PREVIOUS_TEMPLATE = template("judge_previous")
REFUTE_SUSPECTS_TEMPLATE = template("refute_suspects")
PROPOSAL_CHECK_TEMPLATE = template("proposal_check")

TEMPLATES = {
    name: value
    for name, value in globals().items()
    if name.endswith("_TEMPLATE")
}
