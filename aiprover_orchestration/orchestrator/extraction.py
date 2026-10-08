"""Lemma proofs and failed attempts read from AIProver answers.

An AIProver answer is a whole Lean file: the fixed context, the lemma with
the model's proof, and any helper declarations the model added. These
functions take the lemma's proof and helpers out of the answer for the
sketch, and summarize failed answers for the captain.
"""

import re

from ..agents.aiprover import AIProverSample
from ..lean.text import opened_namespaces, split_declarations, strip_comments

# A failed sample as shown to the captain at a replan.
MAX_ATTEMPT_TEXT = 6000
MAX_ATTEMPT_ERRORS = 2000


def answer_helpers(
    blocks: list[tuple[str, str, str]],
    lemma_name: str,
    fixed_names: set[str],
    opened: list[str],
) -> list[str]:
    """The answer's own declarations other than the lemma and the fixed
    context, each under the answer's `open` commands, which they may rely on."""
    prefix = f"open {' '.join(opened)} in\n" if opened else ""
    return [
        prefix + text
        for kind, name, text in blocks
        if name != lemma_name and name not in fixed_names and kind != "example"
    ]


def wrapped_lemma_proof(
    lean_text: str, lemma_name: str, fixed_names: set[str], alias: str
) -> tuple[str, str] | None:
    """(helpers, tactic proof) of `lemma_name` that keeps the answer's own theorem.

    The answer's theorem is kept whole as the helper `alias`, under the
    answer's `open` commands, and the lemma is proved from it by `apply`. This
    recovers proofs that use the answer's binder names or opened namespaces,
    which do not hold under the sketch's statement; Lean still checks the
    sketch's statement.
    """
    blocks = split_declarations(lean_text)
    target = next(
        (
            text
            for kind, name, text in blocks
            if kind in ("theorem", "lemma") and name == lemma_name
        ),
        None,
    )
    if target is None or ":=" not in target:
        return None
    renamed = re.sub(
        rf"((?:theorem|lemma)\s+){re.escape(lemma_name)}(?![\w'.])",
        rf"\g<1>{alias}",
        target,
        count=1,
    )
    opened = opened_namespaces(lean_text)
    helpers = answer_helpers(blocks, lemma_name, fixed_names, opened)
    helpers.append(
        (f"open {' '.join(opened)} in\n" if opened else "") + renamed
    )
    proof = (
        f"first\n  | exact {alias}\n  | (intros; apply {alias} <;> assumption)"
    )
    return "\n\n".join(helpers), proof


def extract_lemma_proof(
    lean_text: str, lemma_name: str, fixed_names: set[str]
) -> tuple[str, str] | None:
    """(helpers, tactic proof) of `lemma_name` from an AIProver answer.

    Helpers are the answer's own declarations other than the lemma and the
    fixed context. A term-mode proof is returned as an `exact` tactic.
    """
    blocks = split_declarations(lean_text)
    target = next(
        (
            text
            for kind, name, text in blocks
            if kind in ("theorem", "lemma") and name == lemma_name
        ),
        None,
    )
    if target is None or ":=" not in target:
        return None
    body = target.split(":=", 1)[1]
    by_match = re.match(r"\s*by\b", body)
    if by_match:
        proof = body[by_match.end() :].strip("\n")
    else:
        proof = "exact (" + body.strip() + ")"
    helpers = answer_helpers(
        blocks, lemma_name, fixed_names, opened_namespaces(lean_text)
    )
    return "\n\n".join(helpers), proof


def failed_attempt(
    sample: AIProverSample, lemma_name: str, fixed_names: set[str]
) -> tuple[tuple[int, int, int], str] | None:
    """A failed sample's own Lean code and Lean's response, for a replan.

    The code is the sample's declarations outside the fixed context: its
    helpers and its version of the lemma. Returns (rank, text), where a lower
    rank is a more informative attempt: some proof written, then fewer Lean
    errors, then fewer `sorry`. A sample that left only `sorry`, without a
    helper or a comment, gives None.
    """
    # Line ranges of the sample's own declarations; comments are kept, since
    # they often hold the plan of an unfinished proof. Line structure is the
    # same in the comment-stripped text that split_declarations reads.
    code_only = strip_comments(sample.lean)
    lines = sample.lean.splitlines()
    ranges, bare = [], True
    for kind, name, text in split_declarations(sample.lean):
        if name in fixed_names and name != lemma_name:
            continue
        if name != lemma_name or re.sub(
            r"\s+", " ", text.split(":=", 1)[-1]
        ).strip() not in ("by sorry", "sorry"):
            bare = False
        first = code_only.count("\n", 0, code_only.find(text)) + 1
        ranges.append((first, first + text.count("\n")))
    code = "\n\n".join(
        "\n".join(lines[first - 1 : last]) for first, last in ranges
    ).strip()
    if not code or (bare and "--" not in code and "/-" not in code):
        return None
    entries = re.split(
        r"\n(?=\S+\.lean:\d+:\d+: )",
        (sample.check or {}).get("diagnostics") or "",
    )
    errors = [
        entry
        for entry in entries
        if (match := re.match(r"\S+\.lean:(\d+):\d+: error", entry))
        and any(first <= int(match.group(1)) <= last for first, last in ranges)
    ]
    holes = len(re.findall(r"\b(?:sorry|admit)\b", code))
    if len(code) > MAX_ATTEMPT_TEXT:
        code = code[:MAX_ATTEMPT_TEXT] + "\n-- (truncated)"
    response = "\n".join(errors)[:MAX_ATTEMPT_ERRORS] or "(no errors)"
    # The harness's status is left out: it also counts the `sorry` of the
    # earlier lemmas, which are stubs in the sample's file.
    text = (
        f"AIProver sample {sample.index} (session {sample.ending or 'finished'}; "
        f"{len(errors)} Lean errors and {holes} sorry in this code)\n"
        f"<lean>\n{code}\n</lean>\nLean errors:\n{response}"
    )
    return (int(bare), len(errors), holes), text
