"""Reading and editing Lean source text without elaborating it."""

import re
import textwrap

# A top-level declaration header, optionally preceded by attributes and
# modifiers.
DECL_START = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?"
    r"(?:(?:private|protected|noncomputable|nonrec)\s+)*"
    r"(theorem|lemma|def|abbrev|instance|example|inductive|structure)\b"
    r"\s*([^\s:({\[]*)",
    re.M,
)
# Lines that end a top-level declaration without starting a new one.
BLOCK_END = re.compile(
    r"^(?:namespace|section|end|open|set_option|#|import|variable)\b", re.M
)
_IMPORT_RE = re.compile(r"^import\s+(\S+)\s*$", re.M)
_DEFINITION_NAME_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+)?"
    r"(?:theorem|lemma|def|abbrev)\s+([^\s:({\[]+)",
    re.M,
)


def strip_comments(lean_text: str) -> str:
    """Remove Lean line and (nested) block comments, keeping line structure."""
    out, depth, i = [], 0, 0
    while i < len(lean_text):
        two = lean_text[i : i + 2]
        if two == "/-":
            depth += 1
            i += 2
        elif two == "-/" and depth:
            depth -= 1
            i += 2
        elif depth:
            out.append("\n" if lean_text[i] == "\n" else "")
            i += 1
        elif two == "--":
            end = lean_text.find("\n", i)
            i = len(lean_text) if end == -1 else end
        else:
            out.append(lean_text[i])
            i += 1
    return "".join(out)


def imports(lean_text: str) -> list[str]:
    """Modules imported by `lean_text`."""
    return _IMPORT_RE.findall(strip_comments(lean_text))


def drop_imports(lean_text: str) -> str:
    return "\n".join(
        line
        for line in lean_text.splitlines()
        if not line.lstrip().startswith("import ")
    )


def split_declarations(lean_text: str) -> list[tuple[str, str, str]]:
    """Top-level declarations of a Lean file as (kind, name, text)."""
    code = strip_comments(lean_text)
    starts = list(DECL_START.finditer(code))
    blocks = []
    for k, match in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(code)
        terminator = BLOCK_END.search(code, match.end(), end)
        if terminator:
            end = terminator.start()
        blocks.append(
            (match.group(1), match.group(2), code[match.start() : end].rstrip())
        )
    return blocks


def declaration_names(lean_text: str) -> list[str]:
    """Names of the theorems, lemmas and definitions declared at line start."""
    return _DEFINITION_NAME_RE.findall(lean_text)


def opened_namespaces(lean_text: str) -> list[str]:
    """Namespaces opened by top-level `open` commands."""
    lines = re.findall(r"^open\s+([^\n]+)$", strip_comments(lean_text), re.M)
    return sorted(
        {name for line in lines for name in line.removesuffix(" in").split()}
    )


def file_scoped(preamble: str) -> str:
    """Rewrite `open X in` / `set_option o v in` as file-level commands.

    A preamble is placed before every declaration of a file, whereas a
    trailing `in` scopes a command to the single declaration that follows.
    """
    return re.sub(
        r"^(\s*(?:open|set_option)\b.*?)\s+in\s*$", r"\1", preamble, flags=re.M
    )


def strip_leading_by(tactics: str) -> str:
    """Remove a leading `by`: tactic blocks are spliced after `:= by`."""
    return re.sub(r"^\s*by\b[ \t]*\n?", "", tactics)


def indent(tactics: str, width: int = 2) -> str:
    """Re-indent a tactic block uniformly under `:= by`."""
    return textwrap.indent(textwrap.dedent(tactics), " " * width)


def normalize(text: str) -> str:
    """`text` with all whitespace runs collapsed to single spaces."""
    return " ".join(text.split())
