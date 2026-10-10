"""Captain/solver orchestration: formalize, audit, sketch, prove, assemble.

The orchestrator model (captain) formalizes the informal result, has the
formalization audited through a blind read-back written by a worker model,
decomposes the proof into lemmas, and assembles the final solution. Worker
models (solvers) prove the lemmas in parallel with Lean error feedback. A
reviewer referees the verified solution and a writer reports it. All Lean
checking is local, in the Lean workspace (`orchestration_workspace/`).

Each stage is implemented in `stages/`; `Orchestration` holds the run's
state, the helpers the stages share, and the driver (`run`).
"""

import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from ..agents import AgentCallError, AgentPool, build_agent
from ..lean.checker import LeanChecker
from ..paths import LOGS_DIR, RESULTS_DIR, TEMP_DIR, WORKSPACE
from . import benchmark, libraries, prompts
from .config import (
    ALGORITHM,
    FILE_HEADER,
    LEAN_ENVIRONMENT,
    Config,
    slug_from_problem,
)
from .resume import ResumeState, restore_state
from .stages.aiprover import AIProverStage
from .stages.assemble import AssembleStage
from .stages.formalize import FormalizeStage
from .stages.prove import ProveStage
from .stages.review import ReviewStage
from .stages.sketch import SketchStage
from .structures import Formalization
from .trace import Trace

logger = logging.getLogger(__name__)

# Summary fields recorded as the trace's outcome.
OUTCOME_FIELDS = (
    "status",
    "error",
    "error_kind",
    "checks",
    "review",
    "report",
    "informal",
    "wall_seconds",
    "phase_seconds",
    "calls_by_role",
    "usage_by_model",
    "lean_attempt_files",
)


class Orchestration(
    FormalizeStage,
    SketchStage,
    ProveStage,
    AIProverStage,
    AssembleStage,
    ReviewStage,
):

    def __init__(
        self, row: dict, config: Config, run_id: str, resume: bool = False
    ):
        """Prepare a run. With `resume`, continue the run recorded in
        results/<run_id>/trace.json from its last completed step."""
        self.row = row
        self.config = config
        self.run_id = run_id
        self.workspace = WORKSPACE
        self.temp_dir = TEMP_DIR / run_id
        self.log_dir = LOGS_DIR / run_id
        self.result_dir = RESULTS_DIR / run_id
        for directory in (self.temp_dir, self.log_dir, self.result_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.lean = LeanChecker(
            self.workspace, config.lean_parallel, config.lean_timeout
        )
        self.slug = slug_from_problem(row)
        self.auditor_system = prompts.auditor_system()
        # The reviewer and the report writer default to fresh agents of the
        # captain's model.
        specs = {
            "reviewer": config.agents.get("captain", {}),
            "writer": config.agents.get("captain", {}),
            **config.agents,
        }
        agents = {role: build_agent(spec) for role, spec in specs.items()}
        system_prompts = {
            "captain": prompts.CAPTAIN_SYSTEM,
            "auditor": self.auditor_system,
            "solver": prompts.SOLVER_SYSTEM,
            "reviewer": prompts.REVIEWER_SYSTEM,
            "writer": prompts.WRITER_SYSTEM,
        }
        trace_path = self.result_dir / "trace.json"
        previous = Trace.load(trace_path) if resume else None
        if resume and previous is None:
            raise FileNotFoundError(f"no trace to resume at {trace_path}")
        if not resume and trace_path.exists():
            raise FileExistsError(
                f"run {run_id} exists; resume it or choose a new run id"
            )
        self.state = restore_state(previous) if previous else ResumeState()
        self.trace = Trace(
            trace_path,
            {
                "run_id": run_id,
                "problem": dict(row),
                "theorem_name": self.slug,
                "models": {
                    "orchestrator": agents["captain"].model,
                    "worker": agents["solver"].model,
                },
                "agents": {
                    role: agent.describe() for role, agent in agents.items()
                },
                "config": asdict(config),
                "algorithm": ALGORITHM,
                "lean_environment": LEAN_ENVIRONMENT,
                "system_prompts": system_prompts,
                "prompt_templates": dict(sorted(prompts.TEMPLATES.items())),
            },
            previous=previous,
        )
        self.agents = AgentPool(
            agents,
            self.log_dir / "calls.jsonl",
            trace=self.trace,
            system_prompt_ids={
                text: key for key, text in system_prompts.items()
            },
            max_claude_calls=config.max_claude_calls,
        )
        self.library_path = config.library or row.get("library", "")
        self.library = self._load_library()
        self._file_counter = self.state.lean_checks_done
        self._counter_lock = threading.Lock()
        previous_outcome = (previous or {}).get("outcome") or {}
        self.phase_times: dict[str, float] = dict(
            previous_outcome.get("phase_seconds") or {}
        )

    # Helpers shared by the stages ------------------------------------------

    def _captain(self, prompt: str, phase: str) -> str:
        return self.agents.complete(
            prompt,
            role="captain",
            phase=phase,
            system_prompt=prompts.CAPTAIN_SYSTEM,
        )

    def _attempt_path(self, label: str) -> Path:
        with self._counter_lock:
            self._file_counter += 1
            index = self._file_counter
        return self.temp_dir / f"{index:04d}_{label}.lean"

    def _check(self, label: str, lean_text: str, **trace_fields):
        """Elaborate a standalone file and record the check in the trace."""
        path = self._attempt_path(label)
        start = time.time()
        result = self.lean.check_text(lean_text, path)
        self.trace.add(
            "lean_check",
            label=label,
            file=path.name,
            source=lean_text,
            ok=result.ok,
            errors=result.errors,
            sorry_warning=result.has_sorry_warning,
            axioms=result.axioms,
            seconds=round(time.time() - start, 1),
            **trace_fields,
        )
        return result

    def _decision(self, event: str, **fields) -> None:
        self.trace.add("decision", event=event, **fields)

    def _load_library(self) -> str:
        """The library text the run started with. It is kept as library.lean
        in the result directory: the library file may be extended by other
        runs meanwhile, and a resumed run elsewhere may lack it."""
        snapshot = self.result_dir / "library.lean"
        if not snapshot.exists():
            if not self.library_path:
                return ""
            library_file = libraries.resolve(self.library_path)
            snapshot.write_text(library_file.read_text())
        return libraries.load(str(snapshot))

    def _check_library(self) -> None:
        """The library must compile on its own without `sorry`."""
        result = self._check("library", FILE_HEADER + self.library + "\n")
        if not result.ok or result.has_sorry_warning:
            raise RuntimeError(
                f"library {self.library_path} does not compile cleanly: "
                + (result.error_report()[:500] if not result.ok else "sorry")
            )

    def _own_definitions(self, form: Formalization) -> str:
        """The run's definitions without the library prefix."""
        if self.library and form.definitions.startswith(self.library):
            return form.definitions[len(self.library) :].strip()
        return form.definitions

    def _library_chars(self) -> int:
        """Length of the library prefix the run's formalization was built
        on."""
        compiled = [
            step
            for step in self.trace.document["steps"]
            if step.get("event") == "formalization_compiled"
        ]
        return (
            compiled[-1].get("library_chars", 0)
            if compiled
            else len(self.library)
        )

    def _standalone(self, form: Formalization, body: str) -> str:
        return f"{FILE_HEADER}{form.definitions}\n\n{form.preamble}\n\n{body}\n"

    @contextmanager
    def _timed(self, phase: str):
        """Attribute the enclosed time to `phase` and tag trace steps."""
        start = time.time()
        self.trace.stage = phase
        logger.info(f"── phase: {phase}")
        try:
            yield
        finally:
            self.phase_times[phase] = (
                self.phase_times.get(phase, 0.0) + time.time() - start
            )

    # Driver ----------------------------------------------------------------

    def run(self) -> dict:
        summary = {
            "run_id": self.run_id,
            "uuid": self.row["uuid"],
            "slug": self.slug,
            "config": asdict(self.config),
            "status": "failed",
        }
        state = self.state
        sketch = None
        sampler = benchmark.MetricsSampler(
            self.result_dir / "metrics.jsonl",
            self.agents.agents["solver"].metrics_url,
        )
        sampler.start()
        if state.restored_steps:
            logger.info(
                f"resuming from step {state.restored_steps}: "
                f"{state.describe()}"
            )
            self._decision("resumed", **state.describe())
        try:
            if self.library_path:
                summary["library"] = {
                    "path": self.library_path,
                    "chars": self._library_chars(),
                }
            if self.library and state.formalization is None:
                self._check_library()
            if state.faithful:
                form = state.formalization
            else:
                with self._timed("formalize"):
                    form = self.formalize(
                        form=state.formalization if not state.audited else None,
                        feedback=state.formalize_feedback,
                        first_round=state.audit_rounds_done,
                    )
            summary["formalization"] = asdict(form)
            if not form.verdict.startswith("FAITHFUL"):
                summary["status"] = "unfaithful_formalization"
                return summary
            replans_used = state.replans_started
            if state.sketch is None:
                with self._timed("sketch"):
                    sketch = self.sketch(form)
            else:
                sketch = state.sketch
                if state.replan_pending:
                    with self._timed("sketch"):
                        sketch = self.replan(form, sketch, resumed=True)
            while True:
                with self._timed("prove"):
                    self.prove(form, sketch)
                if all(lemma.proved for lemma in sketch.lemmas):
                    break
                if replans_used >= self.config.max_replans:
                    summary["status"] = "lemmas_unproved"
                    with self._timed("informal"):
                        summary["informal"] = self.informalize(
                            form, sketch, standalone=None
                        )
                    return summary
                logger.info("some lemmas failed; replanning")
                with self._timed("sketch"):
                    sketch = self.replan(form, sketch)
                replans_used += 1
            with self._timed("assemble"):
                standalone, checks = self.assemble(form, sketch)
            summary["checks"] = checks
            summary["status"] = "assembly_failed"
            (self.result_dir / f"{self.slug}_standalone.lean").write_text(
                standalone
            )
            if not checks["verified"]:
                return summary
            self._export_jiatu_row(standalone)
            # A verified proof stands even if the review or the report cannot
            # be completed; its status then says so.
            summary["status"] = "review_flagged"
            if self.agents.max_claude_calls:
                self.agents.max_claude_calls = (
                    self.agents.claude_calls + self.config.final_claude_calls
                )
            with self._timed("review"):
                review = self.final_review(form, standalone)
            summary["review"] = review
            if review["verdict"] == "FAITHFUL":
                summary["status"] = "proved"
            with self._timed("report"):
                summary["report"] = self.report(
                    form, sketch, standalone, checks, review
                )
            with self._timed("informal"):
                summary["informal"] = self.informalize(form, sketch, standalone)
            if (
                self.config.extend_library
                and self.library_path
                and summary["status"] == "proved"
            ):
                summary["library_extension"] = self._extend_library(
                    form, standalone
                )
            return summary
        except AgentCallError as error:
            summary.update(error=str(error), error_kind="infrastructure")
            logger.error(str(error))
            return summary
        except RuntimeError as error:
            summary.update(error=str(error), error_kind="budget")
            logger.error(str(error))
            return summary
        except (KeyboardInterrupt, SystemExit):
            summary.update(error="interrupted", error_kind="interrupted")
            logger.error("run interrupted; resume with --resume")
            raise
        finally:
            sampler.stop()
            self._write_summary(summary, sketch)
            try:
                benchmark.summarize(self.result_dir, self.trace.document)
            except (OSError, ValueError, KeyError, TypeError) as error:
                logger.warning(f"benchmark summary not written: {error}")

    def _extend_library(self, form: Formalization, standalone: str) -> dict:
        record = libraries.extend(
            self.library_path,
            standalone,
            self._library_chars(),
            form.theorem_name,
            f"{self.run_id} ({self.row['uuid']}), proved",
        )
        logger.info(
            f"library {self.library_path}: "
            + (
                f"added {len(record['declarations'])} declarations"
                if record["added"]
                else f"not extended: {record['problem'][:200]}"
            )
        )
        self._decision("library_extended", library=self.library_path, **record)
        return record

    def _write_summary(self, summary: dict, sketch) -> None:
        """Complete `summary`, write summary.json and close the trace."""
        if sketch is not None:
            summary["sketch"] = {
                "main_proof": sketch.main_proof,
                "lemmas": [asdict(lemma) for lemma in sketch.lemmas],
            }
        summary["wall_seconds"] = self.trace.elapsed_seconds
        summary["phase_seconds"] = {
            phase: round(seconds, 1)
            for phase, seconds in self.phase_times.items()
        }
        summary["calls_by_role"] = self.agents.num_calls
        summary["usage_by_model"] = self.agents.usage_by_model
        summary["lean_attempt_files"] = self._file_counter
        (self.result_dir / "summary.json").write_text(
            json.dumps(summary, indent=2)
        )
        self.trace.finish({key: summary.get(key) for key in OUTCOME_FIELDS})

    def _export_jiatu_row(self, standalone: str) -> None:
        """Write the result in the JiatuBook `_output.jsonl` format for
        evaluate.py."""
        row = dict(self.row)
        row["INFERENCE_DONE"] = True
        row["LLM_Output#1"] = f"```lean4\n{standalone}```"
        path = (
            self.result_dir
            / "orchestration-zeroShot-val_JiatuBook_unlabelled_output.jsonl"
        )
        path.write_text(json.dumps(row) + "\n")
