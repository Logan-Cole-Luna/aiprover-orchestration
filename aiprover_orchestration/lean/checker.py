"""Lean 4 elaboration and module builds in a Lake workspace.

Standalone files are elaborated with `lake env lean <file>`, which resolves
Mathlib from the workspace's `.lake` build without taking the Lake build
lock, so several checks can run concurrently. Library modules are built with
`lake build <module>`; module files are written and built under one file
lock, because concurrent writers share the workspace's sources and build
directory.

Elaboration runs code: `#eval` and its relatives execute with the rights of
the process. Every check and build therefore passes two layers. Text with a
construct that runs code (`EXECUTION_PATTERNS`) is rejected unelaborated, and
Lean runs under the sandbox of `sandbox.py` (Landlock on Linux, Seatbelt on
macOS): it may write only in the workspace, read only the toolchain, the
packages and the checked file's directory, and open no TCP connection.
"""

import fcntl
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..limits import LEAN, lean_megabytes
from ..utils.processes import ProcessStopped, run_group
from .text import strip_comments

STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}

# Keywords of commands that declare or extend, reserved by Lean: a bare
# occurrence anywhere is the command, however it is introduced (`open X in`).
def _command(*keywords: str) -> str:
    return r"(?<![\w.'«])(" + "|".join(keywords) + r")\b"


# An attribute list, given to a declaration or by the `attribute` command.
_ATTRIBUTE_LIST = r"(?:@|\battribute\s*)\[[^\]]*"

# Constructs that run code during elaboration (beyond tactics and the
# kernel). No proof needs them; they are rejected before any elaboration.
EXECUTION_PATTERNS = {
    "#eval": r"#eval\b",
    "#guard": r"#guard\b",
    "unsafe": r"\bunsafe\w*",
    "initialize": _command("builtin_initialize", "initialize"),
    "simproc": r"\b(d?simproc|simproc_decl)\b",
    "IO": r"\b(E|Base)?IO\b",
    "System": r"\bSystem\.\w",
    "Process": r"\bProcess\.\w",
    "evalExpr/evalConst": r"\beval(Expr|Const|Decl)\b",
    "elab": _command("elab", "elab_rules"),
    "run_cmd": r"\b(run_cmd|run_tac|run_elab|run_meta)\b",
    "extension attribute": (
        _ATTRIBUTE_LIST + r"\b(command_elab|term_elab|tactic|init|builtin_init|"
        r"norm_num|positivity|simproc|env_linter|code_action)\b"
    ),
    "implemented_by": r"implemented_by",
    "extern": _ATTRIBUTE_LIST + r"\bextern\b",
    "native_decide": r"\bnative_decide\b",
}

# Constructs that could discharge or bypass a goal without a proof, and those
# that run code. They are rejected in any submitted Lean text before it is
# elaborated.
FORBIDDEN_PATTERNS = {
    **EXECUTION_PATTERNS,
    "sorry": r"\bsorry\b",
    "sorryAx": r"\bsorryAx\b",
    "admit": r"\badmit\b",
    "axiom": _command("axiom"),
    "opaque": _command("opaque"),
    "macro/syntax/elab": _command(
        "macro", "macro_rules", "syntax", "elab", "elab_rules"
    ),
    "debug.skipKernelTC": r"debug\.skipKernelTC",
}

# Problem reported for a compiled proof that still uses `sorry`.
SORRY_PROBLEM = "the proof uses `sorry` (for example through `stop`)"

# `lean` prints `<file>:<line>:<col>: error: ...`; `lake build` prints
# `error: <file>:<line>:<col>: ...`.
_MESSAGE_RE = re.compile(
    r"^(?:(?P<lake_severity>error|warning|info): )?"
    r"(?P<file>[^\n:]+\.lean):(?P<line>\d+):(?P<col>\d+):"
    r"(?: (?P<severity>error|warning|info)(?:\([^)]*\))?:)?",
    re.M,
)
_AXIOMS_RE = re.compile(
    r"'(?P<name>[^']+)' depends on axioms: \[(?P<axioms>[^\]]*)\]"
)
_NO_AXIOMS_RE = re.compile(r"'(?P<name>[^']+)' does not depend on any axioms")
_DECLARATION_RE = re.compile(
    r"^(?:(?:private|protected|noncomputable)\s+)*"
    r"(?:theorem|lemma|def|abbrev|instance|structure|inductive|class)\s+"
    r"(?P<name>[^\s:({\[]+)"
)


@dataclass
class CheckResult:
    ok: bool  # elaborated with no errors
    output: str  # raw Lean output
    errors: list[str] = field(default_factory=list)
    has_sorry_warning: bool = False
    # (file, line) of each "declaration uses 'sorry'" warning.
    sorry_warnings: list[tuple[str, int]] = field(default_factory=list)
    axioms: dict[str, list[str]] = field(default_factory=dict)
    timed_out: bool = False
    # The checked text, when the checker wrote it (`check_text`).
    source: str = ""

    def error_report(self, max_chars: int = 4000) -> str:
        """Errors in a compact form for feeding back to a model. With the
        checked text at hand, each position is given as the declaration
        that contains it and the line itself with the column marked, so
        that a model that never saw the checked file finds it in its own
        text."""
        if not self.errors:
            return self.output[:max_chars]
        errors = self.errors
        if self.source:
            lines = self.source.split("\n")
            errors = [
                _MESSAGE_RE.sub(
                    lambda match: located(lines, match), error, count=1
                )
                for error in errors
            ]
        return "\n\n".join(errors)[:max_chars]


def located(lines: list[str], match: re.Match) -> str:
    """The position of a Lean message `match` within `lines`, as the
    enclosing declaration and the source line with a caret under the
    column."""
    number, column = int(match.group("line")), int(match.group("col"))
    if not 0 < number <= len(lines):
        return match.group(0)
    start, found = next(
        (
            (index, found)
            for index in range(number - 1, -1, -1)
            if (found := _DECLARATION_RE.match(lines[index]))
        ),
        (0, None),
    )
    place = (
        f"in `{found.group('name')}`, line {number - start} of it"
        if found
        else f"line {number}"
    )
    severity = match.group("severity") or match.group("lake_severity")
    return (
        f"{place}, column {column + 1}:\n    {lines[number - 1]}\n"
        f"    {' ' * column}^\n{severity or 'error'}:"
    )


def executable_constructs(lean_text: str) -> list[str]:
    """Names of constructs in `lean_text` that run code when elaborated."""
    code = strip_comments(lean_text)
    return [
        name
        for name, pattern in EXECUTION_PATTERNS.items()
        if re.search(pattern, code, flags=re.M)
    ]


def forbidden_constructs(lean_text: str) -> list[str]:
    """Names of forbidden constructs present in `lean_text` outside comments."""
    code = strip_comments(lean_text)
    return [
        name
        for name, pattern in FORBIDDEN_PATTERNS.items()
        if re.search(pattern, code, flags=re.M)
    ]


def forbidden_problems(
    lean_text: str, where: str = "", statements: bool = False
) -> list[str]:
    """A problem for each forbidden construct of `lean_text`, naming
    `where` it occurs; `statements` admits the `sorry` of statements left
    unproved (`:= by sorry`)."""
    location = f" in {where}" if where else ""
    return [
        f"forbidden construct{location}: {name}"
        for name in forbidden_constructs(lean_text)
        if not (statements and name == "sorry")
    ]


def nonstandard_axioms(
    result: CheckResult, declaration: str
) -> list[str] | None:
    """Axioms of `declaration` outside the standard three.

    Returns None when the output reports no axioms for `declaration`.
    """
    if declaration not in result.axioms:
        return None
    return [
        axiom
        for axiom in result.axioms[declaration]
        if axiom not in STANDARD_AXIOMS
    ]


def _split_messages(output: str) -> list[tuple[str, str, str, int]]:
    """Split Lean output into (severity, message, file, line) tuples."""
    matches = list(_MESSAGE_RE.finditer(output))
    messages = []
    for k, match in enumerate(matches):
        end = matches[k + 1].start() if k + 1 < len(matches) else len(output)
        severity = match.group("severity") or match.group("lake_severity")
        messages.append(
            (
                severity,
                output[match.start() : end].strip(),
                match.group("file"),
                int(match.group("line")),
            )
        )
    return messages


def sorry_after(
    result: CheckResult, first_line: int, path: Path | None = None
) -> bool:
    """True if a declaration at or after `first_line` (of `path`, for a
    build of several files) uses `sorry`: a file's own code, after stubs
    that may use it."""
    return any(
        line >= first_line and (path is None or Path(file).name == path.name)
        for file, line in result.sorry_warnings
    )


SANDBOX = Path(__file__).with_name("sandbox.py")


def sandboxed(
    command: list[str],
    writable: Path,
    readable: list[Path] = (),
    cpu_seconds: int = 0,
) -> list[str]:
    """`command` confined by `sandbox.py`: it writes only in `writable` and
    reads the toolchain, `writable`'s packages and `readable`."""
    packages = (Path(writable) / ".lake" / "packages").resolve()
    arguments = [sys.executable, "-I", str(SANDBOX), "--write", str(writable)]
    if cpu_seconds:
        arguments += ["--cpu-seconds", str(cpu_seconds)]
    for path in [packages, *readable]:
        arguments += ["--read", str(path)]
    return [*arguments, "--", *command]


def lean_command(*files) -> list[str]:
    """Elaboration of `files`, under the memory limit of one Lean process."""
    return ["lake", "env", "lean", f"-M{lean_megabytes()}", *map(str, files)]


def rejected(constructs: list[str]) -> CheckResult:
    """The result of text refused before elaboration."""
    message = (
        "rejected before elaboration: constructs that run code are not "
        f"allowed ({', '.join(constructs)})"
    )
    return CheckResult(ok=False, output=message, errors=[message])


def communicate(
    command: list[str], cwd: Path, timeout: int, stop: threading.Event | None
) -> tuple[int, str] | str:
    """Run `command`; its exit code and output, or why it was ended: a
    timeout or `stop`. The group ends with it, since `lake env` runs `lean`
    as a child."""
    try:
        done = run_group(
            command, cwd=cwd, timeout=timeout, stop=stop, merge_stderr=True
        )
        return done.returncode, done.stdout
    except ProcessStopped:
        return "Lean check stopped: the run is terminating"
    except subprocess.TimeoutExpired:
        return f"Lean timed out after {timeout}s"


def run_lean(
    command: list[str],
    workspace: Path,
    timeout: int,
    readable: list[Path] = (),
    stop: threading.Event | None = None,
    cpu_seconds: int = 0,
    wait: float | None = None,
) -> tuple[int, str] | str:
    """`communicate` for a Lean or Lake command in the sandbox, on one of
    the machine's Lean slots, for which it waits `wait` seconds at most."""
    with LEAN.slots(1, stop, wait=wait) as slot:
        if not slot and not (stop and stop.is_set()):
            return f"no Lean slot free within {wait}s; the machine is busy"
        return communicate(
            sandboxed(command, workspace, readable, cpu_seconds),
            workspace,
            timeout,
            stop,
        )


def _run(
    command: list[str],
    workspace: Path,
    timeout: int,
    readable: list[Path] = (),
    stop: threading.Event | None = None,
) -> CheckResult:
    """Run a Lean or Lake command with `run_lean` and parse its messages."""
    ran = run_lean(command, workspace, timeout, readable, stop)
    if isinstance(ran, str):
        return CheckResult(ok=False, output=ran, errors=[ran], timed_out=True)
    returncode, output = ran
    output = output.strip()
    messages = _split_messages(output)
    errors = [text for severity, text, _, _ in messages if severity == "error"]
    if returncode != 0 and not errors:
        errors = [output or f"exited with code {returncode}"]
    axioms = {
        match.group("name"): [
            axiom.strip()
            for axiom in match.group("axioms").split(",")
            if axiom.strip()
        ]
        for match in _AXIOMS_RE.finditer(output)
    }
    axioms.update(
        {match.group("name"): [] for match in _NO_AXIOMS_RE.finditer(output)}
    )
    return CheckResult(
        ok=not errors,
        output=output,
        errors=errors,
        has_sorry_warning="declaration uses 'sorry'" in output,
        sorry_warnings=[
            (file, line)
            for severity, text, file, line in messages
            if severity == "warning" and "declaration uses 'sorry'" in text
        ],
        axioms=axioms,
    )


class LeanChecker:
    """Elaborates standalone Lean files in the workspace environment."""

    def __init__(
        self,
        workspace: Path,
        max_parallel: int = 4,
        timeout: int = 300,
        stop: threading.Event | None = None,
    ):
        """`stop`, once set, ends the checks in flight and those waiting."""
        self.workspace = Path(workspace)
        self.timeout = timeout
        self.stop = stop
        self._slots = threading.Semaphore(max_parallel)

    def check_file(self, path: Path) -> CheckResult:
        path = Path(path).resolve()
        constructs = executable_constructs(path.read_text())
        if constructs:
            return rejected(constructs)
        with self._slots:
            return _run(
                lean_command(path),
                self.workspace,
                self.timeout,
                [path.parent],
                self.stop,
            )

    def check_text(self, lean_text: str, path: Path) -> CheckResult:
        """Write `lean_text` to `path` and elaborate it."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(lean_text)
        result = self.check_file(path)
        result.source = lean_text
        return result


@contextmanager
def build_lock(workspace: Path):
    """Exclusive lock over the workspace's module sources and build."""
    with open(Path(workspace) / ".lake_build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def build(
    workspace: Path, sources: dict[Path, str], target: str, timeout: int = 600
) -> CheckResult:
    """Write module `sources` (path → text) and build module `target`."""
    constructs = sorted(
        {
            name
            for text in sources.values()
            for name in executable_constructs(text)
        }
    )
    if constructs:
        return rejected(constructs)
    with build_lock(workspace):
        for path, text in sources.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return _run(["lake", "build", target], Path(workspace), timeout)
