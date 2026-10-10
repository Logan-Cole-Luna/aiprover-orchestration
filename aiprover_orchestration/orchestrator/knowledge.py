"""Knowledge carried between AIProver attempts on a lemma.

A session that does not prove its lemma still learns something: which
Mathlib declarations and files it searched, how far its code got and in
which direction its last reasoning pointed. Without a record, the next
session on the same lemma repeats the same search from the start.

`session_notes` condenses one finished session into a short note.
`carry` keeps a lemma's most recent notes, newest first, within MAX_CHARS.
The next job on the lemma receives them as guidance (`hint`), and the captain
sees them at a hand-back, where it may keep, rewrite or clear them when the
solvers are heading the wrong way.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Verbal memory of failed trials carried into the next attempt:
  Reflexion (Shinn et al., NeurIPS 2023),
  https://github.com/noahshinn/reflexion
"""

import json
import re

from ..agents.aiprover import AIProverSample

SEARCH_TOOLS = {
    "grep": "pattern",
    "m_lean_local_search": "query",
    "m_lean_loogle": "query",
    "m_lean_leansearch": "query",
    "m_lean_leanfinder": "query",
    "m_lean_state_search": "file_path",
    "m_lean_hammer_premise": "file_path",
    "m_lean_declaration_file": "symbol",
}
READ_TOOLS = {"read_file": "file_path"}
MAX_SEARCHES = 20
MAX_READS = 12
MAX_REASONING = 900
MAX_CODE = 1500
MAX_CHARS = 6000  # carried notes per lemma
HEADER = (
    "Notes from earlier attempts on this lemma, kept by the coordinator. "
    "Build on what they found; do not repeat searches that found nothing."
)
SEPARATOR = "\n\n---\n\n"


def _argument(call: dict, key: str) -> str:
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return ""
    value = arguments.get(key) if isinstance(arguments, dict) else None
    return str(value or "").strip()


def _shorten_path(path: str) -> str:
    """A Mathlib path from `Mathlib/` on; other paths by their file name."""
    match = re.search(r"(Mathlib/.*)", path)
    return match.group(1) if match else path.rsplit("/", 1)[-1]


def _outcome(query: str, result: str) -> str:
    """How a search ended, when it found nothing."""
    if result.strip() and not re.search(
        r"match_count: 0\b|No matches|no results", result[:400]
    ):
        return ""
    if "\\|" in query:
        return " (no matches: grep is ripgrep, where `\\|` is a literal `|`)"
    return " (no matches)"


def _tool_trail(session: list[dict]) -> tuple[list[str], list[str]]:
    """Searches (with whether they returned anything) and files read, in
    order, from a session transcript."""
    searches, reads, pending = [], [], []
    for entry in session:
        if entry.get("role") == "assistant":
            pending = list(entry.get("tool_calls") or [])
            continue
        if entry.get("role") != "tool" or not pending:
            continue
        call = pending.pop(0)
        name, result = call.get("name") or "", entry.get("text") or ""
        refused = "denied" in result[:200] or "<tool_error" in result[:200]
        if name in SEARCH_TOOLS:
            query = _argument(call, SEARCH_TOOLS[name])
            if query and not refused:
                searches.append(
                    f"{name} `{query[:80]}`{_outcome(query, result)}"
                )
        elif name in READ_TOOLS:
            path = _argument(call, READ_TOOLS[name])
            if "Mathlib/" in path and not refused:
                reads.append(_shorten_path(path))
    return (
        list(dict.fromkeys(searches))[:MAX_SEARCHES],
        list(dict.fromkeys(reads))[:MAX_READS],
    )


def session_notes(sample: AIProverSample, attempt: int, code: str) -> str:
    """A note on one finished session; empty for a session that never ran
    (cancelled, lost server)."""
    if sample.status in ("cancelled", "infra") or not (
        sample.session or sample.reasoning
    ):
        return ""
    searches, reads = _tool_trail(sample.session)
    reasoning = [
        record.get("reasoning", "").strip()
        for record in sample.reasoning
        if (record.get("reasoning") or "").strip()
    ]
    lines = [
        f"Attempt {attempt}, session {sample.index}: {sample.status}, "
        f"ended by {sample.ending or 'the agent'}"
        + (f" after {sample.turns} turns" if sample.turns else "")
    ]
    if searches:
        lines.append("Searched: " + "; ".join(searches))
    if reads:
        lines.append("Read: " + ", ".join(reads))
    if reasoning:
        lines.append(
            "Last reasoning: " + reasoning[-1][-MAX_REASONING:].strip()
        )
    lines.append(
        "Code at the end:\n" + code[:MAX_CODE]
        if code
        else "Code at the end: the statement with `sorry` only."
    )
    return "\n".join(lines)


def carry(previous: str, notes: list[str]) -> str:
    """The notes to carry: the new ones first, then the previous ones, the
    oldest dropped beyond MAX_CHARS."""
    entries = [note for note in notes if note] + (
        previous.split(SEPARATOR) if previous else []
    )
    kept, size = [], 0
    for entry in entries:
        if size + len(entry) > MAX_CHARS and kept:
            break
        kept.append(entry[:MAX_CHARS])
        size += len(entry) + len(SEPARATOR)
    return SEPARATOR.join(kept)


def hint(knowledge: str) -> str:
    """The guidance a job receives from a lemma's carried notes."""
    return f"{HEADER}\n\n{knowledge}" if knowledge else ""
