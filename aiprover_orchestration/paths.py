"""Locations of the repository's data, configuration and runtime state."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WORKSPACE = ROOT / "orchestration_workspace"  # Lake project of all modules
CONFIG_DIR = ROOT / "configs"
DATA_DIR = ROOT / "data"  # problem datasets (JSONL)
LIBRARIES_DIR = ROOT / "libraries"  # single-file result libraries
RESULTS_DIR = ROOT / "results"  # per-run outputs
LOGS_DIR = ROOT / "logs"
TEMP_DIR = ROOT / "temp"
STATE_DIR = ROOT / "state"  # databases of server and library

LIBRARY_DATABASE = STATE_DIR / "library.db"

# AIProver: the plugin (submodule) and its runtime (venvs, ripgrep, jobs).
AIPROVER_CLI = ROOT / "AIProver" / "AIProver_plugin" / "bin" / "aiprover"
AIPROVER_RUNTIME = ROOT / "aiprover"
AIPROVER_JOBS = AIPROVER_RUNTIME / "work" / "jobs"
