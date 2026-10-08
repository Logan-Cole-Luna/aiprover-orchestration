"""Lean 4 elaboration and module builds in a Lake workspace.

Standalone files are elaborated with `lake env lean <file>`, which resolves
Mathlib from the workspace's `.lake` build without taking the Lake build
lock, so several checks can run concurrently. Library modules are built with
`lake build <module>`; module files are written and built under one file
lock, because concurrent writers share the workspace's sources and build
directory.
"""

import fcntl
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .text import strip_comments

STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}

# Constructs that could discharge or bypass a goal without a proof. They are
# rejected in any submitted Lean text before it is elaborated.
FORBIDDEN_PATTERNS = {
    "sorry": r"\bsorry\b",
    "admit": r"\badmit\b",
    "axiom": r"^\s*(private\s+|protected\s+)?axiom\b",
    "opaque": r"^\s*(private\s+|protected\s+)?opaque\b",
    "implemented_by": r"implemented_by",
    "extern": r"@\[\s*extern",
    "native_decide": r"\bnative_decide\b",
    "macro/syntax/elab": (
        r"^\s*(local\s+|scoped\s+)?"
        r"(macro|macro_rules|syntax|elab|elab_rules)\b"
    ),
    "run_cmd": r"\b(run_cmd|run_tac|run_elab)\b",
    "debug.skipKernelTC": r"debug\.skipKernelTC",
}

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


@dataclass
class CheckResult:
    ok: bool  # elaborated with no errors
    output: str  # raw Lean output
    errors: list[str] = field(default_factory=list)
    has_sorry_warning: bool = False
    axioms: dict[str, list[str]] = field(default_factory=dict)
    timed_out: bool = False

    def error_report(self, max_chars: int = 4000) -> str:
        """Errors in a compact form for feeding back to a model."""
        text = "\n\n".join(self.errors) if self.errors else self.output
        return text[:max_chars]


def forbidden_constructs(lean_text: str) -> list[str]:
    """Names of forbidden constructs present in `lean_text` outside comments."""
    code = strip_comments(lean_text)
    return [
        name
        for name, pattern in FORBIDDEN_PATTERNS.items()
        if re.search(pattern, code, flags=re.M)
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


def _split_messages(output: str) -> list[tuple[str, str]]:
    """Split Lean output into (severity, message) pairs."""
    matches = list(_MESSAGE_RE.finditer(output))
    messages = []
    for k, match in enumerate(matches):
        end = matches[k + 1].start() if k + 1 < len(matches) else len(output)
        severity = match.group("severity") or match.group("lake_severity")
        messages.append((severity, output[match.start() : end].strip()))
    return messages


def _run(command: list[str], workspace: Path, timeout: int) -> CheckResult:
    """Run a Lean or Lake command and parse its messages."""
    try:
        proc = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        message = f"Lean timed out after {timeout}s"
        return CheckResult(
            ok=False, output=message, errors=[message], timed_out=True
        )
    output = (proc.stdout + proc.stderr).strip()
    errors = [
        text
        for severity, text in _split_messages(output)
        if severity == "error"
    ]
    if proc.returncode != 0 and not errors:
        errors = [output or f"exited with code {proc.returncode}"]
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
        axioms=axioms,
    )


class LeanChecker:
    """Elaborates standalone Lean files in the workspace environment."""

    def __init__(
        self, workspace: Path, max_parallel: int = 4, timeout: int = 300
    ):
        self.workspace = Path(workspace)
        self.timeout = timeout
        self._slots = threading.Semaphore(max_parallel)

    def check_file(self, path: Path) -> CheckResult:
        command = ["lake", "env", "lean", str(Path(path).resolve())]
        with self._slots:
            return _run(command, self.workspace, self.timeout)

    def check_text(self, lean_text: str, path: Path) -> CheckResult:
        """Write `lean_text` to `path` and elaborate it."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(lean_text)
        return self.check_file(path)


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
    with build_lock(workspace):
        for path, text in sources.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return _run(["lake", "build", target], Path(workspace), timeout)
