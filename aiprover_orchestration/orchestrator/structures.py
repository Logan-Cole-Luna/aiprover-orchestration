"""Data structures of a run and parsing of model-written Lean text."""

import re
from dataclasses import dataclass

from ..lean.text import file_scoped, strip_leading_by


@dataclass
class Formalization:
    definitions: str
    preamble: str  # `open` lines preceding the statement
    theorem_name: str
    signature: str  # binders and type, from after the name to `:=`
    notes: str = ""
    readback: str = ""
    verdict: str = ""
    issues: str = ""

    @property
    def statement(self) -> str:
        return f"theorem {self.theorem_name}{self.signature}:= by sorry"


@dataclass
class Lemma:
    name: str
    statement: str  # `theorem name <binders> : <type>`
    helpers: str = ""
    proof: str = ""
    proved: bool = False
    last_errors: str = ""
    last_attempts: str = ""  # the solvers' last failed code, for a replan
    attempts: int = 0  # completed AIProver jobs that did not prove it
    handed_back: bool = False  # handed back to the captain for this statement
    reviewed_at: int = 0  # attempts when the captain last asked for a retry
    knowledge: str = ""  # notes carried to the next jobs (knowledge.py)
    generation: int = 0  # hand-back splits that led to this lemma


@dataclass
class Sketch:
    lemmas: list[Lemma]
    main_proof: str


# ── Parsing ────────────────────────────────────────────────────────────────


def extract_tag(text: str, tag: str) -> str:
    """Content of the last <tag>...</tag> block, with Markdown fences removed."""
    return extract_block(text, tag).strip()


def extract_block(text: str, tag: str) -> str:
    """Content of the last <tag>...</tag> block without Markdown fences and
    surrounding blank lines; its first line keeps its indentation, to which
    the indentation of the other lines is relative."""
    matches = re.findall(rf"<{tag}>(.*?)</{tag}>", text, flags=re.S)
    if not matches:
        return ""
    body = re.sub(r"\A\s*```[a-zA-Z0-9]*[ \t]*\n", "", matches[-1])
    body = re.sub(r"\n?[ \t]*```\s*\Z", "", body)
    return re.sub(r"\A(?:[ \t]*\n)+", "", body).rstrip()


def extract_tactics(text: str, tag: str) -> str:
    """The tactic block of <tag>, without a leading `by`."""
    return strip_leading_by(extract_block(text, tag))


_DECL_RE = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+([^\s:({\[]+)(.*?):=\s*(?:by\s+)?sorry\b",
    flags=re.S | re.M,
)


def parse_statement(block: str) -> tuple[str, str, str]:
    """Split a statement block into (preamble, theorem name, signature)."""
    matches = list(_DECL_RE.finditer(block))
    if len(matches) != 1:
        raise ValueError(
            "the statement block must contain exactly one "
            "`theorem <name> ... := by sorry` declaration"
        )
    match = matches[0]
    preamble = file_scoped(block[: match.start()].strip())
    return preamble, match.group(1), match.group(2)


def parse_lemmas(block: str) -> list[Lemma]:
    lemmas = []
    for match in _DECL_RE.finditer(block):
        name, signature = match.group(1), match.group(2)
        lemmas.append(
            Lemma(name=name, statement=f"theorem {name}{signature.rstrip()}")
        )
    return lemmas


# ── Lemmas of a sketch ─────────────────────────────────────────────────────


def refers_to(text: str, name: str) -> bool:
    return (
        re.search(rf"(?<![\w.']){re.escape(name)}(?![\w'])", text)
        is not None
    )


def missing_lemmas(
    lemma: Lemma, names: set[str], present: set[str]
) -> list[str]:
    """Lemmas among `names` (of this or an earlier sketch) that the proof of
    `lemma` uses but that are not in `present`: a proof kept across a replan
    is valid only with the lemmas it was proved from."""
    text = f"{lemma.helpers}\n{lemma.proof}"
    return sorted(
        name
        for name in names - present - {lemma.name}
        if refers_to(text, name)
    )


def dependency_order(lemmas: list[Lemma]) -> list[Lemma]:
    """Lemmas in sketch order, except that each proved lemma follows the
    lemmas its proof uses. A proof kept across a replan may use a lemma the
    new sketch places after it. A cycle leaves the rest in sketch order."""

    def uses(lemma: Lemma, other: Lemma) -> bool:
        text = f"{lemma.helpers}\n{lemma.proof}" if lemma.proved else ""
        return refers_to(text, other.name)

    remaining, ordered = list(lemmas), []
    while remaining:
        ready = next(
            (
                lemma
                for lemma in remaining
                if not any(
                    uses(lemma, other)
                    for other in remaining
                    if other is not lemma
                )
            ),
            remaining[0],
        )
        remaining.remove(ready)
        ordered.append(ready)
    return ordered


def failed_lemma_text(lemma: Lemma) -> str:
    """A failed lemma as listed in the replan and hand-back prompts."""
    parts = [
        lemma.statement,
        f"Last Lean errors:\n{lemma.last_errors or '(none recorded)'}",
    ]
    if lemma.last_attempts:
        parts.append(
            f"<last_attempts>\n{lemma.last_attempts}\n</last_attempts>"
        )
    return "\n".join(parts)
