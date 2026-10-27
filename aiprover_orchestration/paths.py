"""Locations of the repository's data, configuration and runtime state.

Code, configuration and data are read from the repository (ROOT). Runtime
state is written under HOME_DIR: the repository itself, or with
AIPROVER_ORCHESTRATION_HOME a per-user directory, so that a user's query
server (scripts/user/aiprover-server) runs from a shared, read-only checkout.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_DIR = Path(os.environ.get("AIPROVER_ORCHESTRATION_HOME") or ROOT).resolve()

CONFIG_DIR = ROOT / "configs"
DATA_DIR = ROOT / "data"  # problem datasets (JSONL)

WORKSPACE = HOME_DIR / "orchestration_workspace"  # Lake project of all modules
LIBRARIES_DIR = HOME_DIR / "libraries"  # single-file result libraries
RESULTS_DIR = HOME_DIR / "results"  # per-run outputs
LOGS_DIR = HOME_DIR / "logs"
TEMP_DIR = HOME_DIR / "temp"
STATE_DIR = HOME_DIR / "state"  # databases of server and library

LIBRARY_DATABASE = STATE_DIR / "library.db"

# AIProver: the harness (the AIProver/ submodule, or the checkout named by
# AIPROVER_HARNESS or by the aiprover/harness link that ./setup --harness
# writes, e.g. the harness/ of the central AIProver repository) and its
# runtime (venvs, ripgrep, jobs).
AIPROVER_RUNTIME = ROOT / "aiprover"
HARNESS_DIR = Path(
    os.environ.get("AIPROVER_HARNESS")
    or (
        AIPROVER_RUNTIME / "harness"
        if (AIPROVER_RUNTIME / "harness").exists()
        else ROOT / "AIProver"
    )
).resolve()
AIPROVER_CLI = HARNESS_DIR / "AIProver_plugin" / "bin" / "aiprover"
# The CLI path run configurations name for the AIProver/ submodule.
DEFAULT_AIPROVER_CLI = "AIProver/AIProver_plugin/bin/aiprover"
AIPROVER_JOBS = HOME_DIR / "aiprover" / "work" / "jobs"

# Top-level directories of HOME_DIR: relative paths into them (such as a
# run's `libraries/<name>.lean`) are resolved there, all others in ROOT.
STATE_NAMES = frozenset(
    {"orchestration_workspace", "libraries", "results", "logs", "temp", "state"}
)


def locate(path: str | Path) -> Path:
    """An absolute path for a path relative to the repository."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    base = HOME_DIR if candidate.parts[:1] and candidate.parts[0] in STATE_NAMES else ROOT
    return base / candidate
