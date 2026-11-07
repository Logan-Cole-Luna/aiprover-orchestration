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

# The repository's Lean and Mathlib release, and the release of this process:
# the server sets it per run (lean/releases.py).
DEFAULT_LEAN_RELEASE = "v4.23.0"
LEAN_RELEASE = os.environ.get("AIPROVER_LEAN_RELEASE") or DEFAULT_LEAN_RELEASE
RELEASES_DIR = HOME_DIR / "lean_releases"


def own_workspace(home: Path, release: str) -> Path:
    """The Lake project of this home's modules on Lean `release`: a copy of
    the release's workspace (lean/releases.py) on its packages. Releases may be
    shared between users' homes, whose module builds must not meet."""
    own = home / "workspaces" / release
    template = home / "lean_releases" / release / "workspace"
    if not (own / "lakefile.lean").exists() and template.is_dir():
        for folder in ("Definitions", "Theorems", "Solutions", ".lake"):
            (own / folder).mkdir(parents=True, exist_ok=True)
        for name in ("lakefile.lean", "lean-toolchain", "lake-manifest.json"):
            (own / name).write_bytes((template / name).read_bytes())
        packages = own / ".lake" / "packages"
        if not packages.is_symlink():
            packages.symlink_to((template / ".lake" / "packages").resolve())
    return own


WORKSPACE = (  # Lake project of all modules
    HOME_DIR / "orchestration_workspace"
    if LEAN_RELEASE == DEFAULT_LEAN_RELEASE
    else own_workspace(HOME_DIR, LEAN_RELEASE)
)
# A package library (orchestrator/libraries.py) is the workspace of the runs
# built on it, and Lean finds its modules through LEAN_PATH, which `lake env`
# extends and lean/sandbox.py passes on, readable only.
LIBRARY_PACKAGE = os.environ.get("AIPROVER_LIBRARY_PACKAGE", "")
if LIBRARY_PACKAGE:
    WORKSPACE = Path(LIBRARY_PACKAGE)
    os.environ["LEAN_PATH"] = str(WORKSPACE / ".lake" / "build" / "lib" / "lean")
    # AIProver sessions build their own LEAN_PATH and append this one.
    os.environ["AIPROVER_EXTRA_LEAN_PATH"] = os.environ["LEAN_PATH"]
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
