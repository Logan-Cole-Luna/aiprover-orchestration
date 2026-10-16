"""Reading and editing Lean source text without elaborating it."""

import re
import textwrap

# The grammar of a top-level declaration header, shared by every reader of
# declarations: attributes, modifiers, the kind and the name.
ATTRIBUTES = r"(?:@\[[^\]]*\]\s*)?"
MODIFIERS = r"(?:(?:private|protected|noncomputable|nonrec|partial)\s+)*"
DECLARATION_KINDS = (
    "theorem",
    "lemma",
    "def",
    "abbrev",
    "instance",
    "example",
    "inductive",
    "structure",
    "class",
    "opaque",
    "axiom",
)
KIND = "(?:" + "|".join(DECLARATION_KINDS) + r")\b"
DECL_START = re.compile(
    rf"^{ATTRIBUTES}{MODIFIERS}({KIND})\s*([^\s:({{\[]*)", re.M
)
# Lines that end a top-level declaration without starting a new one.
BLOCK_END = re.compile(
    r"^(?:namespace|section|end|open|set_option|#|import|variable)\b", re.M
)
_IMPORT_RE = re.compile(r"^import\s+(\S+)\s*$", re.M)


# String and character literals, which are kept verbatim: comment markers
# inside them (`"/-"`) do not open a comment. A raw string `r#"..."#` ends
# at a quote followed by as many `#` as opened it.
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_RAW_STRING_RE = re.compile(r'r(#*)".*?"\1', re.S)
_CHAR_RE = re.compile(
    r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{?[0-9a-fA-F]+\}?|.)|[^\\'\n])'"
)
# Characters of an identifier: a `'` or `r` after one is part of the name,
# not the start of a literal (`h'`, `succ_r`).
_IDENTIFIER_CHAR = re.compile(r"[\w'!?.]")


def strip_comments(lean_text: str) -> str:
    """Remove Lean line and (nested) block comments, keeping line structure
    and the text of string and character literals."""
    out, depth, i = [], 0, 0
    while i < len(lean_text):
        two = lean_text[i : i + 2]
        literal = None
        if not depth:
            after_name = i > 0 and _IDENTIFIER_CHAR.match(lean_text[i - 1])
            if lean_text[i] == '"':
                literal = _STRING_RE.match(lean_text, i)
            elif lean_text[i] == "r" and not after_name:
                literal = _RAW_STRING_RE.match(lean_text, i)
            elif lean_text[i] == "'" and not after_name:
                literal = _CHAR_RE.match(lean_text, i)
        if literal:
            out.append(literal.group(0))
            i = literal.end()
        elif lean_text[i] == '"' and not depth:
            # An unterminated string runs to the end of the text.
            out.append(lean_text[i:])
            i = len(lean_text)
        elif two == "/-":
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


def declarations(lean_text: str) -> list[tuple[str, str, int]]:
    """Top-level declarations as (kind, name, line); `name` is empty for an
    anonymous instance or an example."""
    code = strip_comments(lean_text)
    return [
        (kind, name, code.count("\n", 0, match.start()) + 1)
        for match in DECL_START.finditer(code)
        for kind, name in [match.groups()]
    ]


def declaration_names(lean_text: str, kinds=DECLARATION_KINDS) -> list[str]:
    """Names declared at top level by declarations of the given kinds."""
    return [
        name
        for kind, name, _ in declarations(lean_text)
        if name and kind in kinds
    ]


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


# Lean's default `maxHeartbeats`, and the highest limit kept from an answer:
# a proof that needs more is rejected, which bounds the cost of later builds.
DEFAULT_HEARTBEATS = 200000
MAX_HEARTBEATS = 400000


def heartbeat_option(lean_text: str) -> str:
    """`set_option maxHeartbeats N in` (with a newline) if `lean_text` raises
    the limit above Lean's default, capped at MAX_HEARTBEATS; else empty. The
    limit 0 (none) counts as the cap."""
    limits = [
        int(limit) or MAX_HEARTBEATS
        for limit in re.findall(
            r"^\s*set_option\s+maxHeartbeats\s+(\d+)",
            strip_comments(lean_text),
            re.M,
        )
    ]
    limit = min(MAX_HEARTBEATS, max(limits, default=0))
    if limit <= DEFAULT_HEARTBEATS:
        return ""
    return f"set_option maxHeartbeats {limit} in\n"


def strip_leading_by(tactics: str) -> str:
    """Remove a leading `by`: tactic blocks are spliced after `:= by`."""
    return re.sub(r"^\s*by\b[ \t]*\n?", "", tactics)


def indent(tactics: str, width: int = 2) -> str:
    """Re-indent a tactic block uniformly under `:= by`."""
    return textwrap.indent(textwrap.dedent(tactics), " " * width)


def normalize(text: str) -> str:
    """`text` with all whitespace runs collapsed to single spaces."""
    return " ".join(text.split())
