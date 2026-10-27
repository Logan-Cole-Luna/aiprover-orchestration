"""`lean-check FILE...`: the one shell command of a Claude agent with Lean
tools (agents/claude_cli.py).

Each FILE must be a `.lean` file inside the working directory, a scratch Lake
project. Every Lean file of the project is checked for constructs that run
code; if none is found, the files are elaborated with `lake env lean` under
the sandbox of `sandbox.py`, which may write only in the project.

Run with `python3 -I`, so that nothing in the project or the environment is
imported in place of the orchestration's modules.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiprover_orchestration.lean.checker import (  # noqa: E402
    executable_constructs,
    sandboxed,
)

CPU_SECONDS = 900


def project_files(project: Path) -> list[Path]:
    """The project's Lean files, outside its `.lake` directory."""
    files = []
    for directory, subdirectories, names in os.walk(project):
        subdirectories[:] = [
            name for name in subdirectories if name != ".lake"
        ]
        files += [
            Path(directory) / name for name in names if name.endswith(".lean")
        ]
    return files


def main(arguments: list[str]) -> None:
    project = Path.cwd().resolve()
    targets = [(project / argument).resolve() for argument in arguments]
    for target in targets:
        if target.suffix != ".lean" or not target.is_relative_to(project):
            sys.exit(f"lean-check: {target} is not a Lean file of the project")
        if not target.is_file():
            sys.exit(f"lean-check: {target} does not exist")
    if not targets:
        sys.exit("usage: lean-check FILE.lean...")
    for path in project_files(project):
        constructs = executable_constructs(path.read_text(errors="replace"))
        if constructs:
            sys.exit(
                f"lean-check: rejected {path.relative_to(project)}: "
                f"constructs that run code are not allowed "
                f"({', '.join(constructs)})"
            )
    command = sandboxed(
        ["lake", "env", "lean", *map(str, targets)],
        project,
        cpu_seconds=CPU_SECONDS,
    )
    os.execv(command[0], command)


if __name__ == "__main__":
    main(sys.argv[1:])
