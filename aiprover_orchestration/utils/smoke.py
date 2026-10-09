"""End-to-end smoke test of the library with Claude Haiku as the solver.

    python -m aiprover_orchestration.utils.smoke [--model MODEL] [--keep]

Uses a fresh database under temp/smoke/ and item names with a unique
prefix, and removes the generated workspace modules afterwards (unless
--keep). Model calls go through the `claude` backend (`agents/claude.py`,
key in `ANTHROPIC_API_KEY`).
"""

import argparse
import re
import shutil
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from .. import roles
from ..agents.pool import build_agent
from ..library import api
from ..library.store import Store
from ..library.verifier import Library, Rejected
from ..paths import TEMP_DIR, WORKSPACE

SMOKE_DIR = TEMP_DIR / "smoke"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_ATTEMPTS = 4
_CODE_BLOCK_RE = re.compile(r"```(?:lean4?|)\s*\n(.*?)```", re.S)

PROVE_TEMPLATE = """Prove the theorem in the Lean module below.
You have no tools; answer with the file directly.

Reply with one ```lean code block holding a complete Lean file that:
- starts with the import lines of the module below, unchanged;
- declares `theorem solution` with exactly the type of `{name}`.
Do not import the theorem's own module and do not use `sorry`.

```lean
{module}
```
"""
REPAIR_TEMPLATE = """Your file was rejected:

{error}

Reply with the corrected complete file in one ```lean code block."""


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def expect(condition: bool, description: str) -> None:
    if not condition:
        raise AssertionError(description)
    log(f"ok: {description}")


def prove_with_model(library: Library, name: str, model: str) -> dict:
    """Ask the model for a proof of `name`, repairing from Lean errors."""
    theorem = library.store.item(name, by="name")
    system_prompt = roles.load("solver") + "\n\n" + roles.load("skill")
    prompt = PROVE_TEMPLATE.format(name=name, module=theorem["lean"])
    agent = build_agent({"backend": "claude", "model": model, "timeout": 300})
    submission = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        completion = agent.complete_once(prompt, system_prompt)
        if completion.error:
            raise RuntimeError(f"model call failed: {completion.error}")
        match = _CODE_BLOCK_RE.search(completion.text)
        content = match.group(1) if match else completion.text
        submission = library.verify(
            name, content, explanation=f"{model}, attempt {attempt}"
        )
        log(f"{name} attempt {attempt}: {submission['status']}")
        if submission["status"] != "REJECTED":
            return submission
        prompt += (
            "\n\n"
            + completion.text
            + "\n\n"
            + REPAIR_TEMPLATE.format(error=submission["error"][:3000])
        )
    return submission


def run(model: str, prefix: str) -> None:
    library = Library(Store(SMOKE_DIR / f"{prefix}.db"))
    double, even = f"{prefix}_double", f"{prefix}_double_even"
    square, two_mul = f"{prefix}_sq_add", f"{prefix}_two_mul"

    # 1. Publishing.
    library.publish("definition", double, f"def {double} (n : ℕ) : ℕ := 2 * n")
    library.publish(
        "theorem",
        even,
        f"import Definitions.Def_{double}\n\n"
        f"theorem {even} (n : ℕ) : Even ({double} n) := by sorry",
        statement_nl="Twice a natural number is even.",
    )
    expect(
        library.store.item(even, by="name")["status"] == "Open",
        "definition and theorem published",
    )
    try:
        library.publish(
            "theorem",
            f"{prefix}_broken",
            f"theorem {prefix}_broken : undefinedConstant := " "by sorry",
        )
        rejected = False
    except Rejected:
        rejected = True
    expect(rejected, "ill-typed theorem rejected")

    # 2. Gating.
    header = f"import Definitions.Def_{double}\n\n"
    statuses = [
        library.verify(
            even,
            header + f"theorem solution (n : ℕ) : "
            f"Even ({double} n) := by sorry",
        )["status"],
        library.verify(
            even, header + "theorem solution : (1 : ℕ) + 1 = 2 " ":= rfl"
        )["status"],
        library.verify(
            even,
            f"import Theorems.Thm_{even}\n\n"
            f"theorem solution (n : ℕ) : Even ({double} n) := "
            f"{even} n",
        )["status"],
    ]
    expect(
        statuses == ["REJECTED"] * 3,
        "sorry, wrong-type and self-importing proofs rejected",
    )

    # 3. Direct proof by the model.
    submission = prove_with_model(library, even, model)
    expect(
        submission["status"] == "ACCEPTED"
        and library.store.item(even, by="name")["status"] == "Proved",
        f"{model} proved {even}",
    )

    # 4. Decomposition and resolution.
    library.publish(
        "theorem",
        square,
        f"theorem {square} (a b : ℕ) : (a + b) ^ 2 = "
        "a ^ 2 + 2 * a * b + b ^ 2 := by sorry",
    )
    library.publish(
        "theorem",
        two_mul,
        f"theorem {two_mul} (a b : ℕ) : 2 * a * b = "
        "a * b + a * b := by sorry",
    )
    sketch = library.verify(
        square,
        f"import Theorems.Thm_{two_mul}\n\n"
        "theorem solution (a b : ℕ) : (a + b) ^ 2 = "
        f"a ^ 2 + 2 * a * b + b ^ 2 := by\n  rw [{two_mul}]\n  ring",
    )
    square_id = library.theorem_id(square)
    leaves = library.store.open_leaves(square_id)
    expect(
        sketch["status"] == "SKETCH_ACCEPTED"
        and library.store.item(square_id)["status"] == "Open"
        and [leaf["name"] for leaf in leaves] == [two_mul]
        and leaves[0]["closability"] == 1,
        "sketch accepted with one open leaf",
    )
    submission = prove_with_model(library, two_mul, model)
    expect(
        submission["status"] == "ACCEPTED"
        and library.store.item(square_id)["status"] == "Proved"
        and library.store.submission(sketch["id"])["status"] == "ACCEPTED",
        f"{model} proved {two_mul}; {square} resolved through the sketch",
    )

    # 5. HTTP interface.
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_library] = lambda: library
    response = TestClient(app).get(f"/api/library/theorems/{square_id}/graph")
    graph = response.json()
    expect(
        response.status_code == 200
        and graph["status"] == "Proved"
        and graph["decompositions"][0]["children"][0]["name"] == two_mul,
        "graph served over HTTP",
    )


def remove_modules(prefix: str) -> None:
    for path in WORKSPACE.glob(f"*/*_{prefix}_*.lean"):
        path.unlink()
    for build_dir in (WORKSPACE / ".lake" / "build").glob("**/"):
        for path in build_dir.glob(f"*_{prefix}_*"):
            path.unlink() if path.is_file() else shutil.rmtree(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the database and generated modules",
    )
    arguments = parser.parse_args()
    prefix = f"smoke{int(time.time())}"
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        run(arguments.model, prefix)
        log("smoke test passed")
    finally:
        if not arguments.keep:
            remove_modules(prefix)
            (SMOKE_DIR / f"{prefix}.db").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
