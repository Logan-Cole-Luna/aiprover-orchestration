"""Publishing library items and verifying proof submissions.

Publishing compiles a definition or theorem statement as its own module.
Verification elaborates a submitted `theorem solution` and checks it against
its target in a separate check module. A submission that imports Open
theorems is accepted as a sketch; it proves its target once those children
are proved, which `resolve` propagates up the decomposition graph.

Gating rules for a submission:
1. It declares a top-level `theorem solution`.
2. It does not import its own target theorem.
3. It contains no `sorry` or other forbidden construct (`lean/checker.py`).
4. It imports only Mathlib and published library items; a disproof imports
   no theorems.
"""

import re
from pathlib import Path

from ..lean.checker import build, forbidden_constructs, nonstandard_axioms
from ..lean.layout import (
    NAME_RE,
    SOLUTION_NAME,
    check_module,
    module_name,
    module_path,
    with_header,
)
from ..lean.text import imports, strip_comments
from ..paths import WORKSPACE
from .store import Store, implied_proofs

# Imported Open theorems carry `sorry`; a sketch's proof depends on it only
# through them.
SKETCH_AXIOM = "sorryAx"
SORRY_STATEMENT_RE = re.compile(r":=\s*by\s+sorry\s*$")


class Rejected(ValueError):
    """A request that fails validation or compilation."""


class Library:
    """A library of definitions and theorems over one Lean workspace."""

    def __init__(
        self,
        store: Store | None = None,
        workspace: Path = WORKSPACE,
        timeout: int = 600,
    ):
        self.store = store or Store()
        self.workspace = Path(workspace)
        self.timeout = timeout

    # Publishing ----------------------------------------------------------

    def publish(
        self,
        kind: str,
        name: str,
        lean: str,
        title: str = "",
        statement_nl: str = "",
        source: str = "",
        tags: list[str] = (),
    ) -> dict:
        """Compile and record a definition or an Open theorem."""
        if kind not in ("definition", "theorem"):
            raise Rejected(f"unknown kind {kind!r}")
        if not NAME_RE.match(name):
            raise Rejected(f"invalid name {name!r}")
        if self.store.item(name, by="name"):
            raise Rejected(f"{name} already exists")
        text = with_header(lean)
        problems, _ = self._import_problems(text, theorems_allowed=False)
        problems += self._statement_problems(kind, name, text)
        if problems:
            raise Rejected("; ".join(problems))

        module = module_name(kind, name)
        path = module_path(module, self.workspace)
        result = build(self.workspace, {path: text}, module, self.timeout)
        if not result.ok or (kind == "definition" and result.has_sorry_warning):
            path.unlink(missing_ok=True)
            raise Rejected(
                result.error_report()
                if not result.ok
                else "definition uses sorry"
            )
        item_id = self.store.add_item(
            kind=kind,
            name=name,
            module=module,
            title=title,
            statement_nl=statement_nl,
            lean=text,
            source=source,
            tags=list(tags),
            status="Open" if kind == "theorem" else "Definition",
        )
        return self.store.item(item_id)

    @staticmethod
    def _statement_problems(kind: str, name: str, text: str) -> list[str]:
        constructs = forbidden_constructs(text)
        if kind == "definition":
            return [f"forbidden: {', '.join(constructs)}"] if constructs else []
        code = strip_comments(text)
        constructs = [c for c in constructs if c != "sorry"]
        problems = [f"forbidden: {', '.join(constructs)}"] if constructs else []
        if not re.search(rf"^theorem\s+{re.escape(name)}\b", code, re.M):
            problems.append(f"no top-level `theorem {name}`")
        if not SORRY_STATEMENT_RE.search(code):
            problems.append("statement must end in `:= by sorry`")
        return problems

    def _import_problems(
        self, text: str, theorems_allowed: bool, target: dict | None = None
    ) -> tuple[list[str], list[dict]]:
        """Import violations of `text` and the theorems it imports."""
        problems, theorems = [], []
        for module in imports(text):
            if module == "Mathlib" or module.startswith("Mathlib."):
                continue
            item = self.store.item(module, by="module")
            if item is None:
                problems.append(f"unknown import {module}")
            elif target and item["id"] == target["id"]:
                problems.append("imports its own target theorem")
            elif item["kind"] == "theorem" and not theorems_allowed:
                problems.append(f"may not import theorem {item['name']}")
            elif item["status"] == "Disproved":
                problems.append(f"imports disproved theorem {item['name']}")
            elif item["kind"] == "theorem":
                theorems.append(item)
        return problems, theorems

    # Verification --------------------------------------------------------

    def verify(
        self,
        theorem: int | str,
        content: str,
        proof_type: str = "prove",
        explanation: str = "",
    ) -> dict:
        """Check a submitted proof (or disproof) of `theorem` and record it."""
        target = self.store.item(
            theorem, by="id" if isinstance(theorem, int) else "name"
        )
        if target is None or target["kind"] != "theorem":
            raise Rejected(f"no theorem {theorem!r}")
        if proof_type not in ("prove", "disprove"):
            raise Rejected(f"unknown proof type {proof_type!r}")
        disproof = proof_type == "disprove"
        text = with_header(content)
        submission_id = self.store.add_submission(
            theorem_id=target["id"],
            proof_type=proof_type,
            content=text,
            status="PENDING",
            explanation=explanation,
        )

        problems, children = self._import_problems(
            text, theorems_allowed=not disproof, target=target
        )
        if not re.search(
            rf"^theorem\s+{SOLUTION_NAME}\b", strip_comments(text), re.M
        ):
            problems.append(f"no top-level `theorem {SOLUTION_NAME}`")
        constructs = forbidden_constructs(text)
        if constructs:
            problems.append(f"forbidden: {', '.join(constructs)}")
        if problems:
            return self._finish(submission_id, "REJECTED", "; ".join(problems))

        submission_key = f"{target['name']}_{submission_id}"
        solution = module_name("solution", submission_key)
        check = module_name("check", submission_key)
        solution_path = module_path(solution, self.workspace)
        check_path = module_path(check, self.workspace)
        sources = {
            solution_path: text,
            check_path: check_module(
                target["module"], solution, target["name"], disproof
            ),
        }
        try:
            result = build(self.workspace, sources, check, self.timeout)
        except OSError as error:
            return self._finish(submission_id, "ERROR", str(error))
        finally:
            check_path.unlink(missing_ok=True)
        if not result.ok:
            solution_path.unlink(missing_ok=True)
            return self._finish(
                submission_id, "REJECTED", result.error_report()
            )

        axioms = result.axioms.get(SOLUTION_NAME, [])
        unexpected = set(nonstandard_axioms(result, SOLUTION_NAME) or [])
        if children:
            unexpected.discard(SKETCH_AXIOM)
        if unexpected:
            solution_path.unlink(missing_ok=True)
            return self._finish(
                submission_id,
                "REJECTED",
                f"nonstandard axioms: {sorted(unexpected)}",
            )

        self.store.add_edges(submission_id, {c["id"] for c in children})
        if any(child["status"] != "Proved" for child in children):
            return self._finish(submission_id, "SKETCH_ACCEPTED", axioms=axioms)
        if target["status"] == "Open":
            self.store.set_status(
                target["id"], "Disproved" if disproof else "Proved"
            )
        return self._finish(submission_id, "ACCEPTED", axioms=axioms)

    def _finish(
        self,
        submission_id: int,
        status: str,
        error: str = "",
        axioms: list[str] = (),
    ) -> dict:
        self.store.update_submission(
            submission_id, status=status, error=error, axioms=list(axioms)
        )
        self.resolve()
        return self.store.submission(submission_id)

    def resolve(self) -> list[int]:
        """Mark theorems proved through sketches; returns their ids."""
        sketches = self.store.sketches()
        proved = self.store.proved()
        closure = implied_proofs(proved, sketches)
        resolved = []
        for item in self.store.items(closure - proved):
            if item["status"] == "Open":
                self.store.set_status(item["id"], "Proved")
                resolved.append(item["id"])
        for sketch in sketches:
            if (
                sketch["status"] == "SKETCH_ACCEPTED"
                and sketch["children"] <= closure
            ):
                self.store.update_submission(sketch["id"], status="ACCEPTED")
        return resolved

    # Queries -------------------------------------------------------------

    def show(self, name: str) -> dict:
        item = self.store.item(name, by="name")
        if item is None:
            raise Rejected(f"no item {name!r}")
        return {**item, "submissions": self.store.submissions(item["id"])}

    def theorem_id(self, name: str) -> int:
        item = self.store.item(name, by="name")
        if item is None or item["kind"] != "theorem":
            raise Rejected(f"no theorem {name!r}")
        return item["id"]
