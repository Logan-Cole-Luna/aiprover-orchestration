# Source from the checkout in every shell that starts the query server or the
# batch command:  source scripts/slurm/env.sh
#
# Runs execute as Slurm jobs (server/slurm.py). Site-specific values, such as
# the account (SBATCH_ACCOUNT, which sbatch reads) and where the Lean project
# lies, belong in the site files, whose environment is sourced here.
AIPROVER_ORCHESTRATION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# The tracked template configs/slurm/site.env is filled in place; an untracked
# configs/site.env, if it exists, replaces it.
site="$AIPROVER_ORCHESTRATION_ROOT/configs"
[ -f "$site/site.env" ] || site="$site/slurm"
. "$site/site.env"
export AIPROVER_ORCHESTRATION_ROOT
export AIPROVER_RUN_EXECUTOR=slurm
# The harness's Lean project holds Mathlib, whose file count suits a scratch file system.
export AIPROVER_LEAN_PROJECT="${AIPROVER_LEAN_PROJECT:-${SCRATCH:-$AIPROVER_ORCHESTRATION_ROOT}/aiprover_lean_project}"
export VLLM_VENV="${VLLM_VENV:-$(dirname "$AIPROVER_LEAN_PROJECT")/aiprover_vllm_venv}"
# The server job takes whole nodes unless AIPROVER_SERVER_ARGS says otherwise.
[ -z "${AIPROVER_GPUS:-}" ] || export AIPROVER_SERVER_ARGS="${AIPROVER_SERVER_ARGS:---gpus-per-node=$AIPROVER_GPUS --exclusive}"
# `lake exe cache get` fails when ~/.cache/mathlib is a link, so the cache is kept beside the project.
export MATHLIB_CACHE_DIR="${MATHLIB_CACHE_DIR:-$(dirname "$AIPROVER_LEAN_PROJECT")/mathlib-cache}"
export PYTHONPATH="$AIPROVER_ORCHESTRATION_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$AIPROVER_ORCHESTRATION_ROOT/aiprover/venv_tools/bin:$AIPROVER_ORCHESTRATION_ROOT/aiprover/bin:$HOME/.elan/bin:$HOME/.local/bin:$PATH"
[ -n "${SBATCH_ACCOUNT:-}" ] || echo "env.sh: SBATCH_ACCOUNT is unset; jobs are charged to the default account" >&2
