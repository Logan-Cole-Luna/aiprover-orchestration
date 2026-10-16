"""Knowledge carried between AIProver attempts on a lemma.

A session that does not prove its lemma still learns something: which
Mathlib declarations and files it searched, how far its code got and in
which direction its last reasoning pointed. Without a record, the next
session on the same lemma repeats the same search from the start.

`session_notes` condenses one finished session into a short note, including
the gaps it stated as `have ... := by sorry`. `carry` keeps a lemma's most
recent notes, newest first, within MAX_CHARS. The next job on the lemma
receives them as guidance (`hint`), and the captain sees them at a hand-back,
where it may keep, rewrite or clear them when the solvers are heading the
wrong way.

`MathlibMap` gathers, over all sessions of a run, the Mathlib declarations
the sessions found and used and the searches that found nothing. Every job
receives it, so that sessions on other lemmas neither repeat failed searches
nor rediscover the same declarations; it is rebuilt from the trace on resume.
The run's own declarations (definitions, setting facts, lemmas) are kept out
of it: a search of Mathlib for them fails because they are not Mathlib's.

`setting_index` lists those declarations with their line in the session's
`Context.lean`, and `SEARCH_GUIDANCE` directs sessions to semantic search;
both lead the guidance of every job, with the run's learned clauses
(cdcl.py).

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Verbal memory of failed trials carried into the next attempt:
  Reflexion (Shinn et al., NeurIPS 2023),
  https://github.com/noahshinn/reflexion
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field

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
MAX_GAPS = 8
REASONING_LABEL = "Last reasoning: "
CODE_LABEL = "Code at the end:"
MAX_FOUND = 40  # Mathlib map: declarations listed
MAX_ABSENT = 30  # Mathlib map: failed searches listed
# Qualified Lean names (`Finset.sum_le_sum`) and snake-case names
# (`sum_le_sum`), as they appear in code and in search results.
IDENTIFIER = re.compile(
    r"\b[A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)+|\b[a-z][\w']*_[\w']+"
)
DECLARATION = re.compile(
    r"\b(?:theorem|lemma|def|abbrev|instance|structure|class)\s+([\w'.]+)"
)
UNKNOWN = re.compile(r"[Uu]nknown (?:identifier|constant) '([^']+)'")
# Snake-case tactic names, which the identifier pattern also matches.
TACTICS = {
    "simp_rw", "simp_all", "norm_num", "norm_cast", "push_cast", "push_neg",
    "field_simp", "exact_mod_cast", "rw_mod_cast", "split_ifs", "by_contra",
    "by_cases", "apply_fun", "interval_cases", "fin_cases", "set_option",
    "simp_arith", "exact?", "apply?", "le_rfl", "mod_cast",
}
# Hypotheses and bound variables (`h_geom.tsum_eq`, `I.IsMaximal`,
# `mR.mapCotangent`): a first component that is one capital, or lowercase
# and short, unlike Mathlib's namespaces (`Set`, `Nat`, `Ideal`).
LOCAL = re.compile(
    r"^(?:h\w*|[A-Z]|[a-z][A-Za-z0-9']{0,2})(?:\.|$)|^h_|^[a-z]_\w{1,2}$"
)
# Run files that the identifier pattern also matches (`problem.txt`).
FILE_NAME = re.compile(r"\.(?:txt|lean|json|md)$")
# Declarations of the run's own Lean text, with their kind.
RUN_DECLARATION = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:(?:private|protected|noncomputable)\s+)*"
    r"(theorem|lemma|def|abbrev|instance|structure|class|inductive)\s+"
    r"([^\s(:{\[]+)",
    re.M,
)
MAX_SETTING_NAMES = 80
SEARCH_GUIDANCE = (
    "How to search. The declarations listed under 'Defined in the Setting' "
    "are this run's own: read them in `Context.lean` in your working "
    "directory (or in problem.txt); never search Mathlib for them. To find "
    "a Mathlib fact, describe it in words to `m_lean_leanfinder` (semantic "
    "search over this project's Mathlib) first, then use "
    "`m_lean_local_search` or `m_lean_loogle` for a name or a type pattern; "
    "`m_lean_local_search` also covers cslib, the Lean library for computer "
    "science (computability, automata). Use `grep` only for an exact name "
    "you already know, or within `Context.lean`."
)
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


def _tool_results(session: list[dict]):
    """(tool name, call, result text, refused) for each tool call of a
    session transcript, in order. A result is matched to the pending call of
    its tool's name (the transcript interleaves subagents and hook replies),
    and a grep result to the call whose pattern it restates."""
    pending = []
    for entry in session:
        if entry.get("role") == "assistant":
            pending = list(entry.get("tool_calls") or [])
            continue
        if entry.get("role") != "tool" or not pending:
            continue
        name = entry.get("name")
        index = next(
            (
                i
                for i, call in enumerate(pending)
                if not name or call.get("name") == name
            ),
            None,
        )
        if index is None:
            continue
        call = pending.pop(index)
        result = entry.get("text") or ""
        restated = re.search(r"^pattern: (.*)$", result, re.M)
        if restated and restated.group(1).strip() != _argument(
            call, "pattern"
        ):
            continue
        refused = "denied" in result[:200] or "<tool_error" in result[:200]
        yield call.get("name") or "", call, result, refused


def _library_wide(name: str, call: dict) -> bool:
    """True if a search that found nothing shows a name to be absent: a
    Lean search tool, or a grep over all of Mathlib whose pattern can match
    a declaration (not a qualified name, which Mathlib's source writes
    inside its namespace, and not ripgrep's literal `\\|`)."""
    if name != "grep":
        return True
    path = _argument(call, "path").rstrip("/")
    pattern = _argument(call, "pattern")
    return (
        path.endswith((".lake/packages", "/mathlib", "/mathlib/Mathlib"))
        and "\\|" not in pattern
        and not re.search(r"\w\\?\.\w", pattern)
    )


def _tool_trail(session: list[dict]) -> tuple[list[str], list[str]]:
    """Searches (with whether they returned anything) and files read, in
    order, from a session transcript."""
    searches, reads = [], []
    for name, call, result, refused in _tool_results(session):
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


def stated_gaps(code: str) -> list[str]:
    """The facts a session left as `have ... := by sorry`: the gaps it
    reports as missing."""
    gaps = []
    pattern = r"\bhave\b(.*?):=\s*(?:by\s+)?sorry\b"
    for match in re.finditer(pattern, code, re.S):
        text = " ".join(match.group(1).split())
        if text and "have " not in text:
            gaps.append(text[:200])
    return list(dict.fromkeys(gaps))[:MAX_GAPS]


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
    gaps = stated_gaps(code)
    if gaps:
        lines.append(
            "Gaps left as `sorry`: " + "; ".join(f"`{gap}`" for gap in gaps)
        )
    if reasoning:
        lines.append(
            REASONING_LABEL + reasoning[-1][-MAX_REASONING:].strip()
        )
    lines.append(
        f"{CODE_LABEL}\n" + code[:MAX_CODE]
        if code
        else f"{CODE_LABEL} the statement with `sorry` only."
    )
    return "\n".join(lines)


def without_reasoning(knowledge: str) -> str:
    """Carried notes without the sessions' reasoning, for a hosted model."""
    pattern = rf"\n{re.escape(REASONING_LABEL)}.*?(?=\n{re.escape(CODE_LABEL)})"
    return re.sub(pattern, "", knowledge, flags=re.S)


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


@dataclass
class MathlibMap:
    """Mathlib declarations found and used, and searches that found nothing,
    over the sessions of a run, with how often each occurred (experimental:
    library search)."""

    found: Counter = field(default_factory=Counter)
    absent: Counter = field(default_factory=Counter)

    def add(self, session: list[dict], code: str) -> None:
        """Record one session: a name counts as found when a search result
        shows it and the session's code uses it."""
        used = set(IDENTIFIER.findall(code))
        for name, call, result, refused in _tool_results(session):
            for missing in UNKNOWN.findall(result):
                if "." in missing:
                    self.absent[f"`{missing}` (unknown identifier)"] += 1
            if refused or name not in SEARCH_TOOLS:
                continue
            query = _argument(call, SEARCH_TOOLS[name])
            if not query:
                continue
            if _outcome(query, result):
                if _library_wide(name, call):
                    self.absent[f"{name} `{query[:80]}`"] += 1
                continue
            shown = set(IDENTIFIER.findall(result))
            shown |= set(DECLARATION.findall(result))
            for ident in used:
                if ident in TACTICS or LOCAL.search(ident):
                    continue
                if ident in shown or ident.rsplit(".", 1)[-1] in shown:
                    self.found[ident] += 1

    @classmethod
    def from_steps(cls, steps: list[dict]) -> "MathlibMap":
        """The map of the AIProver sessions recorded in a trace."""
        mathlib = cls()
        for step in steps:
            if (
                step["kind"] != "model_call"
                or step.get("backend") != "aiprover"
            ):
                continue
            for sample in step.get("samples") or []:
                mathlib.add(
                    sample.get("session") or [], sample.get("lean") or ""
                )
        return mathlib

    def text(self, exclude: set[str] = frozenset()) -> str:
        """The map as guidance, without the run's own declarations
        (`exclude`): neither as found names nor in failed searches. A failed
        search is listed only while no found name contains its query's
        terms."""
        lines = []
        names = [
            name
            for name, _ in self.found.most_common()
            if name not in exclude
            and name.rsplit(".", 1)[-1] not in exclude
            and not FILE_NAME.search(name)
        ][:MAX_FOUND]
        found_text = " ".join(names).lower()
        own = (
            re.compile(
                r"(?<![\w.'])(?:"
                + "|".join(map(re.escape, sorted(exclude, key=len)[::-1]))
                + r")(?![\w'])"
            )
            if exclude
            else None
        )
        misses = [
            miss
            for miss, _ in self.absent.most_common()
            if not (own and own.search(miss))
            and not any(
                term.lower() in found_text
                for term in IDENTIFIER.findall(miss)
            )
        ][:MAX_ABSENT]
        if names:
            lines.append(
                "Mathlib declarations that earlier sessions of this run found "
                "and used: " + ", ".join(f"`{name}`" for name in names)
            )
        if misses:
            lines.append(
                "Searches over all of Mathlib by earlier sessions of this run "
                "that found nothing; do not repeat them. A fact searched for "
                "several ways without a match is probably absent from this "
                "Mathlib: prove it, or state it as a `have ... := by sorry` "
                "gap: " + "; ".join(misses)
            )
        return "\n".join(lines)


def declared_names(lean_text: str) -> set[str]:
    """Names declared in the run's Lean text, qualified and unqualified."""
    names = set()
    for _, name in RUN_DECLARATION.findall(lean_text):
        names.add(name)
        names.add(name.rsplit(".", 1)[-1])
    return names


def setting_index(context: str) -> str:
    """The declarations of a job's context with their line in
    `Context.lean`, the copy of the context in the session's project."""
    entries = []
    for match in RUN_DECLARATION.finditer(context):
        line = context.count("\n", 0, match.start(2)) + 1
        entries.append(f"`{match.group(2)}` ({match.group(1)}, line {line})")
    if not entries:
        return ""
    shown = entries[:MAX_SETTING_NAMES]
    more = len(entries) - len(shown)
    return (
        "Defined in the Setting (this run's declarations, in `Context.lean` "
        "and problem.txt, not in Mathlib): "
        + ", ".join(shown)
        + (f", and {more} more in `Context.lean`" if more else "")
    )


def hint(
    knowledge: str, mathlib: str = "", setting: str = "", clauses: str = ""
) -> str:
    """The guidance a job receives: the run's declarations and how to
    search, the run's learned clauses, its Mathlib map, and the lemma's
    carried notes."""
    parts = [
        part for part in (setting, SEARCH_GUIDANCE, clauses, mathlib) if part
    ]
    if knowledge:
        parts.append(f"{HEADER}\n\n{knowledge}")
    return "\n\n".join(parts)
