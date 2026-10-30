"""Orchestrator settings a submission may set: a subset of the run
configuration (orchestrator/config.py `Config`), with bounds, and the
values a run takes when it sets none (the served config over the defaults
of `Config`). The worker passes a run's settings to run.py as options, so a
resumed run keeps them.

Not offered: resources of the machine (`lean_parallel`, session slots,
lemma concurrency), conflict-driven learning (pending evaluation), and the
library, which the form sets on its own.
"""

from dataclasses import dataclass, fields

from ..orchestrator.config import Config


@dataclass(frozen=True)
class Setting:
    group: str
    label: str
    minimum: int = 0
    maximum: int = 0
    choices: tuple[str, ...] = ()  # the values of a text setting


SETTINGS = {
    "workers": Setting("Proof search", "Solver chains per lemma", 1, 8),
    "max_repairs": Setting(
        "Proof search", "Lean-error repair rounds per chain", 0, 10
    ),
    "max_replans": Setting(
        "Proof search", "Sketch revisions after failed lemmas", 0, 10
    ),
    "max_audit_rounds": Setting(
        "Proof search", "Formalization revisions after an audit", 0, 5
    ),
    "setting_check": Setting(
        "Proof search", "Test the setting before planning"
    ),
    "sketch_judge": Setting("Proof search", "Judge each compiled sketch"),
    "lean_timeout": Setting(
        "Proof search", "Time limit of a Lean check (s)", 30, 1800
    ),
    "aiprover_attempts_per_lemma": Setting(
        "AIProver", "Failed jobs per lemma statement", 1, 20
    ),
    "aiprover_handback_after": Setting(
        "AIProver", "Failed jobs before a hand-back (0: never)", 0, 20
    ),
    "max_handbacks": Setting(
        "AIProver", "Hand-backs per run (0: unlimited)", 0, 100
    ),
    "aiprover_max_retries": Setting(
        "AIProver", "Retries the orchestrator may grant a lemma (0: unlimited)", 0, 20
    ),
    "aiprover_early_revision": Setting(
        "AIProver", "Hand a lemma back once its attempts are spent"
    ),
    "aiprover_job_sessions": Setting(
        "AIProver", "Sessions per job (0: no limit)", 0, 32
    ),
    "aiprover_timeout": Setting(
        "AIProver", "Time limit of a session (s; 0: none)", 0, 86400
    ),
    "aiprover_probe_seconds": Setting(
        "AIProver",
        "Seconds between checks of running sessions (0: at their end)",
        0,
        7200,
    ),
    "aiprover_direction": Setting(
        "AIProver",
        "Direction for each job: the orchestrator's proof of the lemma, the "
        "sketch's comments, or none",
        choices=("captain", "sketch", ""),
    ),
    "aiprover_cite_lemmas": Setting(
        "AIProver", "Mark earlier lemmas as facts to cite"
    ),
    "aiprover_condensed_prompt": Setting(
        "AIProver", "Condense the prompt of a job with a direction"
    ),
    "aiprover_relevant_context": Setting(
        "AIProver", "Give a job only the lemmas relevant to it"
    ),
    "max_claude_calls": Setting(
        "Claude budget", "Claude calls per run (0: unlimited)", 0, 1000
    ),
    "final_claude_calls": Setting(
        "Claude budget", "Calls added for the review and the report", 0, 50
    ),
    "informal_claude_calls": Setting(
        "Claude budget", "Calls added for the informalization", 0, 50
    ),
}
CONFIG_DEFAULTS = {option.name: option.default for option in fields(Config)}


def kind(name: str) -> type:
    """The type of a setting; `aiprover_timeout`, which the worker applies
    to the solver's specification, is not a field of `Config`."""
    return type(CONFIG_DEFAULTS.get(name, 0))


def catalog(served_config: dict) -> list[dict]:
    """The settings as the form offers them, each with the value of a run
    that does not set it (None: the solver's own time limit)."""
    return [
        {
            "name": name,
            "group": setting.group,
            "label": setting.label,
            "type": kind(name).__name__,
            "minimum": setting.minimum,
            "maximum": setting.maximum,
            "choices": list(setting.choices),
            "default": served_config.get(name, CONFIG_DEFAULTS.get(name)),
        }
        for name, setting in SETTINGS.items()
    ]


def validate(values: dict) -> dict:
    """The submitted settings, checked; ValueError names the problem."""
    for name, value in values.items():
        setting = SETTINGS.get(name)
        if setting is None:
            raise ValueError(f"unknown setting {name!r}")
        expected = kind(name)
        # bool is a subclass of int; a number is no switch, nor the reverse.
        if type(value) is not expected:
            raise ValueError(f"{name}: expected {expected.__name__}")
        if expected is int and not (
            setting.minimum <= value <= setting.maximum
        ):
            raise ValueError(
                f"{name}: {value} is outside "
                f"{setting.minimum}..{setting.maximum}"
            )
        if expected is str and value not in setting.choices:
            raise ValueError(f"{name}: {value!r} is not one of {setting.choices}")
    return dict(values)
