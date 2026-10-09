"""Run configuration, constants and the description stored in each trace."""

import re
from dataclasses import dataclass, field

FILE_HEADER = "import Mathlib\nset_option autoImplicit false\n\n"
MAIN_NAME = "solution"
LEAN_ENVIRONMENT = {
    "toolchain": "leanprover/lean4:v4.23.0",
    "mathlib": "37df177aaa770670452312393d4e84aaad56e7b6",
    "file_header": FILE_HEADER,
}

# Human-readable description of the orchestration, stored in the trace header.
ALGORITHM = [
    {
        "stage": "formalize",
        "actor": "captain (orchestrator model)",
        "description": "Translate the informal statement into Lean 4 definitions "
        "and a theorem statement ending in `sorry`. The file is "
        "compiled; Lean errors are returned to the captain for "
        "repair.",
    },
    {
        "stage": "formalize",
        "actor": "auditor (worker model)",
        "description": "Write a blind read-back of the Lean code, seeing only "
        "the code and the auditor role prompt, never the source.",
    },
    {
        "stage": "formalize",
        "actor": "captain (orchestrator model)",
        "description": "Compare the read-back with the source statement and "
        "proof; verdict FAITHFUL continues, REVISE returns to "
        "formalization.",
    },
    {
        "stage": "sketch",
        "actor": "captain (orchestrator model)",
        "description": "Decompose the proof: lemmas stated in full and left as "
        "`sorry`, plus a sorry-free proof of `solution` from "
        "them. The sketch must compile.",
    },
    {
        "stage": "prove",
        "actor": "solvers (worker model, parallel)",
        "description": "Each lemma is attacked by several independent solver "
        "chains. A chain proposes a proof, receives Lean errors, "
        "and repairs; the first proof that compiles is kept and "
        "the other chains stop.",
    },
    {
        "stage": "sketch",
        "actor": "captain (orchestrator model)",
        "description": "If a lemma is not proved within budget, the captain "
        "revises the sketch; proved lemmas with unchanged "
        "statements are reused.",
    },
    {
        "stage": "assemble",
        "actor": "Lean",
        "description": "Splice proofs into the sketch and verify: compiles, no "
        "`sorry`, only standard axioms, and the solution's type "
        "equals the target theorem's type (checked in a module "
        "importing both).",
    },
    {
        "stage": "review",
        "actor": "reviewer (independent model)",
        "description": "Referee the verified file against the source: "
        "definitions, statement, and the proof's correspondence "
        "with the source proof. A run is proved only with verdict "
        "FAITHFUL.",
    },
    {
        "stage": "report",
        "actor": "writer, reviewer",
        "description": "LaTeX report and PDF: the writer states the mathematics, "
        "the Lean code is inserted verbatim, the reviewer checks "
        "each statement against its Lean code, and the writer "
        "corrects the discrepancies.",
    },
    {
        "stage": "informal",
        "actor": "writer, reviewer",
        "description": "Informalization: the theorem and every lemma with its "
        "proof in natural language, without Lean, also when "
        "lemmas remain unproved; the reviewer checks it against "
        "the Lean source.",
    },
]


@dataclass
class Config:
    # Agent specification per role (captain, auditor, solver, reviewer,
    # writer); see agents/pool.py.
    agents: dict = field(default_factory=dict)
    workers: int = 4  # parallel solver chains per lemma
    max_repairs: int = 4  # Lean-error feedback rounds per chain
    max_replans: int = 2  # sketch revisions after failed lemmas
    max_audit_rounds: int = 2  # formalization revisions after REVISE
    lean_parallel: int = 6
    lean_timeout: int = 300
    max_claude_calls: int = 0  # Claude calls per run; 0 = unlimited
    # AIProver lemma jobs in flight at once; 0 = all pending lemmas. Each job
    # runs `workers` sessions that share one model server.
    aiprover_lemma_concurrency: int = 0
    # AIProver jobs per lemma statement that run to completion; jobs stopped
    # by a lost model server or an interruption do not count.
    aiprover_attempts_per_lemma: int = 1
    # Sessions per model server shared by the AIProver jobs in flight; 0 =
    # `workers` per job.
    # Each job takes a share weighted by its lemma's failed attempts.
    aiprover_session_slots: int = 0
    # Completed failed jobs after which a lemma is handed back to the captain
    # (prove it, split it, restate it, or retry); 0 = never.
    aiprover_handback_after: int = 0
    # Claude calls added to `max_claude_calls` for the final review and the
    # report, so a run that spent its budget on proving is still reviewed.
    final_claude_calls: int = 8
    # Claude calls added for the informalization (write, check, revise,
    # repairs).
    informal_claude_calls: int = 6
    # Library of verified results the run builds on (a Lean file; see
    # libraries.py); overrides the problem's `library` field. With
    # `extend_library`, a reviewed proof is appended to it.
    library: str = ""
    extend_library: bool = False


def slug_from_problem(row: dict) -> str:
    """Theorem name from the book label (e.g. `prop: bounding ite base`)."""
    match = re.search(
        r"book label `[^:`]*:\s*([^`]+)`", row.get("informal_statement", "")
    )
    label = match.group(1) if match else row["uuid"]
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()
