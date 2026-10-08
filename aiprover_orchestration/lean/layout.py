"""Module layout of the Lean workspace.

Every library item is one Lean module: a definition `foo` is
`Definitions.Def_foo`, a theorem `foo` is `Theorems.Thm_foo` (its statement
ending in `:= by sorry`), and submission `n` for `foo` is
`Solutions.Sol_foo_n`, declaring `theorem solution`. A check module
`Solutions.Check_foo_n` imports the target theorem and the solution and
requires their types to agree, so the solution itself never imports its own
target.
"""

import re
from pathlib import Path

from ..paths import WORKSPACE
from .text import imports

MODULE_PREFIXES = {
    "definition": "Definitions.Def_",
    "theorem": "Theorems.Thm_",
    "solution": "Solutions.Sol_",
    "check": "Solutions.Check_",
}
SOLUTION_NAME = "solution"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.']*$")


def module_name(kind: str, name: str) -> str:
    """Module of the item or submission `name` of the given kind."""
    return MODULE_PREFIXES[kind] + re.sub(r"[.']", "_", str(name))


def module_path(module: str, workspace: Path = WORKSPACE) -> Path:
    return Path(workspace) / (module.replace(".", "/") + ".lean")


def with_header(lean_text: str) -> str:
    """`lean_text` with `import Mathlib` added when it imports nothing."""
    text = lean_text.strip() + "\n"
    return text if imports(text) else "import Mathlib\n\n" + text


def check_module(
    target_module: str, solution_module: str, theorem: str, disproof: bool
) -> str:
    """Module requiring `solution` to prove (or refute) `theorem`."""
    claim = f"type_of% @{theorem}"
    expected = f"¬ ({claim})" if disproof else claim
    return (
        f"import {target_module}\n"
        f"import {solution_module}\n\n"
        f"example : {expected} := @{SOLUTION_NAME}\n\n"
        f"#print axioms {SOLUTION_NAME}\n"
    )
