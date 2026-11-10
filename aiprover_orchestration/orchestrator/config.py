"""Run configuration, constants and the description stored in each trace."""

import re
from dataclasses import dataclass, field

from ..paths import DEFAULT_LEAN_RELEASE, LEAN_RELEASE, LIBRARY_PACKAGE, WORKSPACE
from ..utils.vocabulary import modernize

# Every file of a run imports Mathlib and the root modules of its package
# library, if it has one.
LIBRARY_IMPORTS = "".join(
    f"import {root.stem}\n"
    for root in sorted(WORKSPACE.glob("*.lean"))
    if LIBRARY_PACKAGE and root.stem != "lakefile"
)
FILE_HEADER = f"import Mathlib\n{LIBRARY_IMPORTS}set_option autoImplicit false\n\n"
MAIN_NAME = "solution"


def current_names(values: dict) -> dict:
    """`values` with each setting, and each role of its agents, under its
    current name."""
    return modernize(values)


LEAN_ENVIRONMENT = {
    "toolchain": f"leanprover/lean4:{LEAN_RELEASE}",
    "mathlib": (
        "37df177aaa770670452312393d4e84aaad56e7b6"
        if LEAN_RELEASE == DEFAULT_LEAN_RELEASE
        else LEAN_RELEASE
    ),
    "file_header": FILE_HEADER,
}

# Human-readable description of the orchestration, stored in the trace header.
ALGORITHM = [
    {
        "stage": "formalize",
        "actor": "orchestrator",
        "description": "Translate the informal statement into Lean 4 definitions "
        "and a theorem statement ending in `sorry`. The file is "
        "compiled; Lean errors are returned to the orchestrator for "
        "repair.",
    },
    {
        "stage": "formalize",
        "actor": "checker (worker model)",
        "description": "Probe the statement in Lean: contradictory hypotheses, "
        "closure by automation alone, a counterexample, and "
        "equivalence with an independent formalization when the "
        "`formalizer` role is configured. Then write a blind "
        "read-back of the Lean code, seeing only "
        "the code and the checker role prompt, never the source.",
    },
    {
        "stage": "formalize",
        "actor": "judge (independent model), orchestrator",
        "description": "Compare the read-back and the Lean evidence with the "
        "source statement and proof; verdict FAITHFUL of every "
        "judge continues, REVISE returns to formalization.",
    },
    {
        "stage": "sketch",
        "actor": "orchestrator",
        "description": "Test the setting first: facts the orchestrator proves about "
        "it (contradictory hypotheses, a degenerate structure) "
        "join the definitions. Then decompose the proof: lemmas "
        "stated in full and left as `sorry`, plus a sorry-free "
        "proof of `solution` from them. The sketch must compile. "
        "A theorem the orchestrator judges false is refuted instead, "
        "by a compiled proof of its negation.",
    },
    {
        "stage": "prove",
        "actor": "orchestrator",
        "description": "Before a lemma's first AIProver job, write its "
        "informal proof as the job's guidance: numbered steps "
        "naming the hypotheses, earlier lemmas and Mathlib "
        "declarations each uses.",
    },
    {
        "stage": "prove",
        "actor": "provers (worker model, parallel)",
        "description": "Each lemma is attacked by several independent prover "
        "chains. A chain proposes a proof, receives Lean errors, "
        "and repairs; the first proof that compiles is kept and "
        "the other chains stop.",
    },
    # Conflict-driven learning (cdcl.py), disabled pending evaluation:
    # {
    #     "stage": "prove",
    #     "actor": "orchestrator",
    #     "description": "Conflict-driven learning, after CDCL SAT solvers: "
    #     "when several leaves under one decision (a sketch lemma or "
    #     "a split) are stuck, or one is refuted, the orchestrator "
    #     "analyses the conflict, records learned clauses that "
    #     "every later job and prompt receives, and may backjump: "
    #     "the subtree is retracted and the decision restated.",
    # },
    {
        "stage": "sketch",
        "actor": "orchestrator",
        "description": "If a lemma is not proved within budget or is refuted "
        "by a compiled proof of its negation, the orchestrator "
        "revises the sketch; proved lemmas with unchanged "
        "statements are reused.",
    },
    {
        "stage": "assemble",
        "actor": "Lean",
        "description": "Splice proofs into the sketch and verify: compiles, no "
        "`sorry`, only standard axioms, and the solution's type "
        "equals the target theorem's type (checked in a module "
        "importing both). A failure returns to the orchestrator: the "
        "lemmas responsible are escalated, or the sketch is "
        "replanned when none is.",
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
    # Agent specification per role (orchestrator, checker, prover, reviewer,
    # writer); see agents/pool.py.
    agents: dict = field(default_factory=dict)
    # What the run does: "autoformalize" translates the row's informal proof;
    # "prove" finds a proof (stages/draft.py), of the row's formal statement if
    # it has one, kept as given, or else of its formalized informal statement.
    mode: str = "autoformalize"
    # In prove mode the orchestrator first judges whether the statement holds;
    # judged false, the run proves its negation (stages/draft.py).
    disproof: bool = False
    workers: int = 4  # parallel prover chains per lemma
    max_repairs: int = 4  # Lean-error feedback rounds per chain
    # Sketch revisions after failed lemmas; a revision that follows the
    # refutation of a lemma is not counted.
    max_replans: int = 2
    # The orchestrator checks the hypotheses for vacuity before planning
    # (contradictory hypotheses, degenerate structures); facts it proves join
    # the definitions.
    vacuity_check: bool = True
    # A second model judges each compiled sketch; a REVISE verdict counts as a
    # failed repair round, except in the last round.
    sketch_judge: bool = True
    # Lean's `plausible` tests each sketch lemma on random instances before
    # the judge; a counterexample rejects the sketch (stages/sketch.py).
    lemma_counterexamples: bool = False
    # Open steps (stages/gaps.py): the sketch names them `gap_...`; they are
    # attempted after the routine lemmas and remain proof targets, with a
    # Lean-checked reduction recorded once only gaps are left.
    open_subgoals: bool = False
    # Drafts in prove mode, ranked by a judge; a sketch the judge still
    # rejects passes to the next.
    approaches: int = 1
    # The orchestrator proves the routine lemmas it can before AIProver jobs.
    orchestrator_first: bool = False
    max_audit_rounds: int = 2  # formalization revisions after REVISE
    # Lean probes of each audited statement (fidelity.py); a proof of `False`
    # from its hypotheses is a REVISE that no judge can overrule.
    fidelity_probes: bool = True
    # Lean checks of one run; limits.LEAN bounds all runs of the machine.
    lean_parallel: int = 6
    lean_timeout: int = 300
    max_hosted_calls: int = 0  # hosted-model calls per run; 0 = unlimited
    # USD of hosted calls per run at list prices, before the final review,
    # where tokens without a price count at agents.base.TOKENS_PER_USD;
    # 0 = unlimited. No recorded run above 34 USD reached a proof.
    max_hosted_usd: int = 50
    # AIProver lemma jobs in flight at once; 0 = all pending lemmas. Each job
    # runs `workers` sessions that share one model server.
    aiprover_lemma_concurrency: int = 0
    # Failed attempts per lemma statement over the run (AIProver jobs that
    # run to completion, or prove phases of chat provers); jobs stopped by a
    # lost model server or an interruption do not count. A replan may not
    # state an unproved statement whose attempts are spent.
    aiprover_attempts_per_lemma: int = 1
    # Sessions per model server shared by the AIProver jobs in flight; 0 =
    # `workers` per job.
    # Each job takes a share weighted by its lemma's failed attempts.
    aiprover_session_slots: int = 0
    # Sessions of one job, including those added while it runs; 0 = no limit.
    aiprover_job_sessions: int = 0
    # Give a job only the lemmas relevant to its lemma (cdcl.context_lemmas):
    # the sketch's lemmas and those split along its branch, not the
    # internal lemmas of other branches. Every answer repeats its context,
    # so this bounds the size of each session's files.
    aiprover_relevant_context: bool = True
    # Completed failed jobs after which a lemma is escalated to the orchestrator
    # (prove it, split it, restate it, or retry); 0 = never.
    aiprover_escalation_after: int = 0
    # Escalations per run; 0 = unlimited. Conflict analyses count as well.
    max_escalations: int = 0
    # Escalate a lemma once more when its statement has used its attempts,
    # rather than leaving it to the replan at the end of the phase; once per
    # statement, counted as an escalation.
    aiprover_early_revision: bool = True
    # Retries the orchestrator may grant one lemma statement; past it, escalations
    # offer proof, split, restatement or refutation only, and a lemma
    # retried more often goes back to the orchestrator at once. 0 = unlimited.
    aiprover_max_retries: int = 2
    # The guidance every AIProver job on a lemma statement receives ahead
    # of the source's proof: "orchestrator", an informal proof of the lemma the
    # orchestrator writes before the statement's first job (one hosted-model call per
    # statement); "sketch", the comments above the lemma in the orchestrator's
    # reply that stated it (no call); "" for none.
    aiprover_guidance: str = "orchestrator"
    # Keep AIProver sessions off the internet (lean/offline.py), for problems
    # whose answer may be published.
    aiprover_offline: bool = False
    # Give the orchestrator `paper-search` (search/papers.py) over arXiv math
    # abstracts and papers, hiding those submitted from `paper_search_before`
    # (YYYY-MM-DD), the cutoff of a benchmark taken from arXiv.
    paper_search: bool = False
    paper_search_before: str = ""
    # Session time limit of a lemma's first job (s); 0 = the prover's own.
    # A short first job finds the easy lemmas before long jobs start.
    aiprover_first_job_seconds: int = 0
    # Mark the earlier lemmas in a job's context as facts to cite, and list
    # those its guidance names, with their statements, in its prompt.
    aiprover_cite_lemmas: bool = True
    # Condense the prompt of a job with guidance: its fixed context keeps
    # only the earlier lemmas the guidance names, and the source's proof,
    # from which the orchestrator wrote the guidance, is left out.
    aiprover_condensed_prompt: bool = True
    # Seconds between Lean gates of the working files of a job's running
    # sessions; 0 gates a session's answer only when the session ends.
    aiprover_probe_seconds: int = 600
    # Conflict-driven learning (cdcl.py), off by default pending a
    # controlled evaluation and not offered on the server pages: learned
    # clauses and conflict analysis, whose proofs of stuck lemmas are
    # applied at once; backjumping is a separate switch.
    cdcl: bool = False
    cdcl_backjump: bool = False
    # Stuck leaves under one decision that make a conflict; a refuted leaf
    # makes one alone.
    cdcl_conflict_leaves: int = 3
    # Hosted-model calls kept for the final review and the report: the limit rises
    # to leave this many, so a run that spent its budget on proving is still
    # reviewed.
    final_hosted_calls: int = 8
    # Failed assemblies returned to the orchestrator before the run ends.
    max_assembly_rounds: int = 3
    # Hosted-model calls added for the informalization (write, check, revise,
    # repairs).
    informal_hosted_calls: int = 6
    # Write the report and the informalization at the end of a run. A
    # finished run writes them when resumed with this setting.
    write_reports: bool = False
    # Stop after the sketch for a reviewer's decision on the proof plan. The
    # server records the decision: `sketch_feedback`, under a new
    # `plan_revision`, requests a revised plan, and `sketch_guidance` joins
    # the orchestrator's later prompts.
    sketch_approval: bool = False
    sketch_feedback: str = ""
    plan_revision: int = 0
    sketch_guidance: str = ""
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
