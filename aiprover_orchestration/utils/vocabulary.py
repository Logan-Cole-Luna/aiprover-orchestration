"""Former names in configurations, queued jobs, and recorded runs, and their
translation to the current ones. Records are never rewritten; readers
translate them on loading (`modernize`)."""

import re

# Settings under an earlier name.
FORMER_NAMES = {
    "max_claude_usd": "max_hosted_usd",
    "max_claude_calls": "max_hosted_calls",
    "final_claude_calls": "final_hosted_calls",
    "informal_claude_calls": "informal_hosted_calls",
    "setting_check": "vacuity_check",
    "gaps": "open_subgoals",
    "captain_first": "orchestrator_first",
    "aiprover_handback_after": "aiprover_escalation_after",
    "max_handbacks": "max_escalations",
    "aiprover_direction": "aiprover_guidance",
}
# Roles, events, phases, and fields under an earlier name, matched whole.
FORMER_TERMS = {
    **FORMER_NAMES,
    "captain": "orchestrator",
    "solver": "prover",
    "solvers": "provers",
    "auditor": "checker",
    "handback": "escalation",
    "lemma_handback": "lemma_escalation",
    "direction": "guidance",
    "lemma_direction": "lemma_guidance",
    "direction_prompt_cache": "guidance_prompt_cache",
    "setting_checked": "vacuity_checked",
    "solver_budget_exhausted": "prover_budget_exhausted",
    "solver_attempt_rejected": "prover_attempt_rejected",
    "solver_calls": "prover_calls",
    "captain_proofs": "orchestrator_proofs",
    "captain_phases": "orchestrator_phases",
}
# Prompt template names (`HANDBACK_TEMPLATE`) and labels of model calls and
# checks (`handback/<lemma>`, `<lemma>_captain`).
UPPER = {"CAPTAIN": "ORCHESTRATOR", "SOLVER": "PROVER", "AUDITOR": "CHECKER",
         "HANDBACK": "ESCALATION", "SETTING_CHECK": "VACUITY_CHECK",
         "DIRECTION": "GUIDANCE"}
CONSTANT = re.compile(r"[A-Z][A-Z_]*")
LABELS = {"direction/": "guidance/", "handback/": "escalation/",
          "_captain": "_orchestrator"}
LABEL = re.compile(r"^(?:direction|handback)/|_captain$")


def current_term(text: str) -> str:
    if text in FORMER_TERMS:
        return FORMER_TERMS[text]
    if CONSTANT.fullmatch(text):
        for old, new in UPPER.items():
            text = text.replace(old, new)
        return text
    return LABEL.sub(lambda match: LABELS[match.group(0)], text)


def modernize(record):
    """`record` (a parsed trace, summary, or configuration) with former
    names replaced: dictionary keys, and strings without spaces. Text such
    as prompts and Lean code is left as it is."""
    if isinstance(record, dict):
        return {current_term(key): modernize(value) for key, value in record.items()}
    if isinstance(record, list):
        return [modernize(value) for value in record]
    if isinstance(record, str) and len(record) < 200 and " " not in record:
        return current_term(record)
    return record
