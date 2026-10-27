"""Data structures of a run and parsing of model-written Lean text."""

import re
from dataclasses import dataclass

from ..lean.text import (
    ATTRIBUTES,
    KIND,
    MODIFIERS,
    file_scoped,
    normalize,
    split_declarations,
    strip_leading_by,
)


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

    def add_setting_facts(self, facts: str) -> None:
        """Append facts proved by the setting check to the definitions. The
        facts were compiled after the preamble and may rely on its `open`
        commands, so they join the definitions under it."""
        self.definitions = "\n\n".join(
            filter(None, [self.definitions, self.preamble, facts])
        )


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
    refutation: str = ""  # compiled Lean proof of the statement's negation
    parent: str = ""  # the lemma this one was split from (cdcl.py)
    # Lean definitions its statement may use, introduced by splits above it,
    # outermost first; each block is placed once, before the first lemma
    # that needs it (`stub_listing`).
    definitions: tuple[str, ...] = ()


@dataclass
class Sketch:
    lemmas: list[Lemma]
    main_proof: str
    # Compiled Lean proof that the theorem itself is false; such a sketch
    # has no lemmas and ends the run as `disproved`.
    refutation: str = ""


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


def extract_named(text: str, tag: str) -> dict[str, str]:
    """Content of each <tag name="...">...</tag> block by name, the last
    block of a name winning, as `extract_block` returns it."""
    return {
        name: extract_block(f"<{tag}>{body}</{tag}>", tag)
        for name, body in re.findall(
            rf'<{tag}\s+name="([^"]+)"\s*>(.*?)</{tag}>', text, flags=re.S
        )
    }


# A `theorem` or `lemma` ending in `:= sorry`; its signature does not run
# into a later declaration (a line that starts one).
_DECL_RE = re.compile(
    rf"^{ATTRIBUTES}(?:theorem|lemma)\s+([^\s:({{\[]+)"
    rf"((?:(?!^{ATTRIBUTES}{MODIFIERS}{KIND}).)*?)"
    r":=\s*(?:by\s+)?sorry\b",
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


_RECORDED_NAME = re.compile(
    rf"\s*{ATTRIBUTES}(?:theorem|lemma)\s+([^\s:({{\[]+)"
)


def recorded_lemmas(statements: list[str]) -> list[Lemma]:
    """Lemmas from statements a trace records (`theorem name ...`, without a
    proof), each kept exactly as recorded: a resumed run continues with the
    statements its trace holds."""
    lemmas = []
    for statement in statements:
        match = _RECORDED_NAME.match(statement)
        if match:
            lemmas.append(Lemma(match.group(1), statement.rstrip()))
    return lemmas


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


def reinstated(
    earlier: list[Lemma], lemmas: list[Lemma]
) -> list[tuple[Lemma, str]]:
    """Lemmas of the `earlier` sketch that a replan's `lemmas` drop although
    the proof of a proved lemma it keeps (same statement) uses them, with
    the lemma each is inserted before; `lemmas` is updated. A proved lemma
    is thereby kept with the lemmas it was proved from rather than lost
    with them. Dependencies of reinstated proved lemmas return as well; a
    refuted lemma cannot, since a proof that uses it is unsound, nor can a
    lemma whose name the new sketch gives another statement."""
    by_name = {lemma.name: lemma for lemma in earlier}
    kept = {normalize(lemma.statement) for lemma in lemmas}
    pending = [
        lemma
        for lemma in earlier
        if lemma.proved and normalize(lemma.statement) in kept
    ]
    added = []
    while pending:
        user = pending.pop()
        present = {lemma.name for lemma in lemmas}
        for name in missing_lemmas(user, set(by_name), present):
            if by_name[name].refutation:
                continue
            # Before the lemma that states `user`'s proposition, which
            # the new sketch may have renamed.
            position, before = next(
                (index, lemma.name)
                for index, lemma in enumerate(lemmas)
                if normalize(lemma.statement) == normalize(user.statement)
            )
            dependency = by_name[name]
            lemma = Lemma(
                name, dependency.statement, definitions=dependency.definitions
            )
            lemmas.insert(position, lemma)
            present.add(name)
            added.append((lemma, before))
            if dependency.proved:
                pending.append(dependency)
    return added


def statement_key(statement: str) -> str:
    """The proposition a lemma states, without its name: the key under which
    attempts and refutations are counted, so that a renamed lemma is the
    same lemma."""
    match = _RECORDED_NAME.match(statement)
    return normalize(statement[match.end() :] if match else statement)


def statements(lemmas: list[Lemma]) -> dict[str, str]:
    """Lemma name to normalized statement."""
    return {lemma.name: normalize(lemma.statement) for lemma in lemmas}


def unchanged_names(before: dict[str, str], after: dict[str, str]) -> set[str]:
    """Lemmas of `after` stated as in `before` (`statements`): those a proof
    found under `before` may still use."""
    return {name for name, text in after.items() if before.get(name) == text}


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


def stub_listing(
    lemmas: list[Lemma], note=None, before: Lemma | None = None
) -> str:
    """The lemmas as statements left unproved (`:= by sorry`), a block each,
    with the definitions they use; `note(lemma)` is appended to a lemma's
    statement (a comment). `before` is the lemma the listing precedes, whose
    definitions are added if no listed lemma brought them."""
    shown: set[str] = set()
    blocks = []
    for lemma in lemmas:
        blocks += new_definitions(lemma, shown)
        blocks.append(
            f"{lemma.statement} := by sorry" + (note(lemma) if note else "")
        )
    if before is not None:
        blocks += new_definitions(before, shown)
    return "\n\n".join(blocks)


def declared_names(lean_text: str) -> set[str]:
    return {name for _, name, _ in split_declarations(lean_text) if name}


def new_definitions(lemma: Lemma, shown: set[str]) -> list[str]:
    """The definition blocks of `lemma` not in `shown`, which is updated."""
    blocks = [block for block in lemma.definitions if block not in shown]
    shown.update(blocks)
    return blocks


def carry_definitions(lemmas: list[Lemma], earlier: list[Lemma]) -> None:
    """Give each of `lemmas` (a new sketch) the definition blocks of the
    `earlier` sketch that its statement uses, with the blocks they use."""
    blocks = list(
        dict.fromkeys(block for other in earlier for block in other.definitions)
    )
    for lemma in lemmas:
        used, text = [], lemma.statement
        for block in reversed(blocks):
            if any(refers_to(text, name) for name in declared_names(block)):
                used.insert(0, block)
                text += "\n" + block
        lemma.definitions = tuple(used)


def failed_lemma_text(lemma: Lemma) -> str:
    """A failed lemma as listed in the replan and hand-back prompts."""
    if lemma.refutation:
        return (
            f"{lemma.statement}\nThis lemma is FALSE. Lean accepted this "
            f"proof of its negation:\n<refutation>\n{lemma.refutation}\n"
            f"</refutation>"
        )
    parts = [
        lemma.statement,
        f"Last Lean errors:\n{lemma.last_errors or '(none recorded)'}",
    ]
    if lemma.last_attempts:
        parts.append(
            f"<last_attempts>\n{lemma.last_attempts}\n</last_attempts>"
        )
    return "\n".join(parts)
