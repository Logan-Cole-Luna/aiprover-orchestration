#!/bin/bash
# Set up this checkout on a Slurm cluster without Nix, where ./setup cannot
# create /nix. Run it from the checkout on a login node: it performs the steps
# that need the network, which compute nodes lack, and submits the builds as CPU
# jobs, since a login node runs nothing heavy.
#
#   bash scripts/slurm/setup.sh [--claude] [--codex] [--search] [--no-build]
#
# Each step is skipped when its result exists:
#   1. the AIProver/ submodule (the harness)
#   2. virtualenvs with uv: aiprover/venv_tools (server and pipeline),
#      aiprover/venv_vibe and aiprover/venv_mcp (the harness's, from the locks
#      in nix/python/, with the patches of nix/packages.nix)
#   3. ripgrep into aiprover/bin
#   4. elan's Lean toolchain and the harness's Lean project (Mathlib, REPL,
#      cslib) at AIPROVER_LEAN_PROJECT with Mathlib's prebuilt oleans;
#      orchestration_workspace/ links its packages
#   5. with --claude or --codex, that client. Hosted roles run the client on the
#      compute node of the run, which shares the home directory and so the login
#      of the login node (`claude`, then /login; `codex login`), and reaches the
#      provider through the SSH session of the run job.
#      With --search, the virtualenv of the paper search and its arXiv index.
#   6. with AIPROVER_MODEL, the virtualenv VLLM_VENV with vLLM, unless it exists
#   7. CPU jobs: the Lean builds, then the packs of scripts/slurm/pack.sh (the
#      Lean project, and the vLLM virtualenv VLLM_VENV with its Python, if set)
#
# The script first checks its prerequisites. VLLM_VERSION selects the vLLM release.
# configs/aiprover/slurm.toml names these paths. The account is SBATCH_ACCOUNT.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
. "$ROOT/scripts/slurm/env.sh"
CLAUDE=no
CODEX=no
SEARCH=no
BUILD=yes
while [ $# -gt 0 ]; do
  case "$1" in
    --claude) CLAUDE=yes ;;
    --codex) CODEX=yes ;;
    --search) SEARCH=yes ;;
    --no-build) BUILD=no ;;
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
LEAN_TOOLCHAIN="leanprover/lean4:v4.23.0"
LEAN_PROJECT="$AIPROVER_LEAN_PROJECT"
RG_VERSION=14.1.1
# The vLLM release that serves the model (VLLM_VERSION overrides it), and the free space in GB
# that the Lean project, Mathlib files, and the vLLM environment need together.
VLLM_VERSION="${VLLM_VERSION:-0.26.0}"
MIN_FREE_GB=40
export PATH="$ELAN_HOME/bin:$HOME/.local/bin:$PATH"
# Login nodes cap a user's threads; uv sizes its pools by the node's cores
# and panics ("failed to initialize global rayon pool") without these.
export RAYON_NUM_THREADS="${RAYON_NUM_THREADS:-4}" UV_CONCURRENT_INSTALLS="${UV_CONCURRENT_INSTALLS:-4}" \
       UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-8}" UV_CONCURRENT_BUILDS="${UV_CONCURRENT_BUILDS:-2}"
step() { printf '\n==> %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
# Check what the steps below need, and report every problem at once.
preflight() {
  local problems=() parent
  for tool in git curl tar uv; do
    command -v "$tool" >/dev/null || problems+=("$tool is not on PATH${tool/uv/ (curl -LsSf https://astral.sh/uv/install.sh | sh)}")
  done
  [ "$BUILD" = no ] || command -v sbatch >/dev/null || problems+=("sbatch is not on PATH (--no-build skips the jobs)")
  parent=$(dirname "$LEAN_PROJECT")
  mkdir -p "$parent" 2>/dev/null
  [ -w "$parent" ] || problems+=("$parent is not writable; set AIPROVER_LEAN_PROJECT")
  if [ -n "${AIPROVER_MODEL:-}" ] && [ ! -e "$AIPROVER_MODEL/config.json" ] && [ ! -e "$AIPROVER_MODEL/params.json" ]; then
    problems+=("AIPROVER_MODEL=$AIPROVER_MODEL has no config.json or params.json")
  fi
  if [ "${#problems[@]}" -gt 0 ]; then
    printf 'setup: %s\n' "${problems[@]}" >&2
    exit 1
  fi
  if [ "$(df -Pk "$parent" | awk 'NR==2 {print int($4 / 1048576)}')" -lt "$MIN_FREE_GB" ]; then
    echo "setup: warning: less than $MIN_FREE_GB GB is free in $parent" >&2
  fi
}
preflight
# The virtualenvs hold many small files, which suit scratch better than a home directory.
if [ ! -e aiprover ] && [ -n "${SCRATCH:-}" ]; then
  mkdir -p "$SCRATCH/aiprover_envs" && ln -s "$SCRATCH/aiprover_envs" aiprover
fi

step "Harness: AIProver/ submodule"
if [ -x AIProver/AIProver_plugin/bin/aiprover ] || [ -e aiprover/harness ]; then
  ok "present"
else
  git submodule update --init AIProver
fi

step "Virtualenvs"
mkdir -p aiprover
if ! aiprover/venv_tools/bin/python -c 'import fastapi, uvicorn, aiohttp, pygments, anthropic, psutil, httpx' 2>/dev/null; then
  uv venv -q --python 3.12 aiprover/venv_tools
  VIRTUAL_ENV="$ROOT/aiprover/venv_tools" uv pip install -q fastapi uvicorn aiohttp pygments anthropic psutil httpx
fi
ok "aiprover/venv_tools ($(aiprover/venv_tools/bin/python --version))"
for name in vibe mcp; do
  UV_PROJECT_ENVIRONMENT="$ROOT/aiprover/venv_$name" uv sync -q --frozen --python 3.12 --project "nix/python/$name"
  ok "aiprover/venv_$name"
done
# The lean-lsp-mcp fixes nix/packages.nix applies (patch -p1 in site-packages).
SITE=$(echo aiprover/venv_mcp/lib/python3.12/site-packages)
for patch in nix/python/mcp/patches/*.patch; do
  if patch -p1 -d "$SITE" --dry-run -R -s -f < "$patch" >/dev/null 2>&1; then
    ok "already applied: $(basename "$patch")"
  else
    patch -p1 -d "$SITE" -s < "$patch"
    for source in $(sed -n 's|^+++ b/||p' "$patch"); do
      rm -f "$SITE/$(dirname "$source")/__pycache__/$(basename "$source" .py)".*.pyc
    done
    ok "applied: $(basename "$patch")"
  fi
done

step "ripgrep"
if [ -x aiprover/bin/rg ]; then
  ok "present"
else
  mkdir -p aiprover/bin
  tmp=$(mktemp -d)
  curl -sSfL "https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/ripgrep-${RG_VERSION}-x86_64-unknown-linux-musl.tar.gz" -o "$tmp/rg.tgz"
  tar xzf "$tmp/rg.tgz" -C "$tmp" && install -m 0755 "$tmp"/ripgrep-*/rg aiprover/bin/rg
  rm -rf "$tmp"
  ok "aiprover/bin/rg"
fi

step "Lean: $LEAN_TOOLCHAIN and the harness's Lean project"
if [ ! -x "$ELAN_HOME/bin/elan" ]; then
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none --no-modify-path
fi
elan toolchain install "$LEAN_TOOLCHAIN" >/dev/null 2>&1 || true
mkdir -p "$LEAN_PROJECT"
for f in lakefile.lean lake-manifest.json lean-toolchain; do
  [ -f "$LEAN_PROJECT/$f" ] || cp "AIProver/AIProver_plugin/setup/lean_project/$f" "$LEAN_PROJECT/$f"
done
[ -f "$LEAN_PROJECT/TmpProjDir.lean" ] || echo "-- library root (intentionally empty)" > "$LEAN_PROJECT/TmpProjDir.lean"
# An interrupted download leaves a package checkout with an emptied index or
# stray files, and lake then fails to check out its revision; resetting the
# checkout to its HEAD lets lake continue.
repair_packages() {
  for package in "$LEAN_PROJECT"/.lake/packages/*/; do
    [ -e "$package/.git" ] || continue
    if ! git -C "$package" diff --quiet HEAD 2>/dev/null \
       || [ -n "$(git -C "$package" ls-files --others --exclude-standard | head -n 1)" ]; then
      git -C "$package" reset -q --hard HEAD && git -C "$package" clean -fdq
    fi
  done
}
# Fetching Mathlib's prebuilt files hashes every file of the project, which is
# slow on a shared file system, so it runs once; the marker records that it did.
if [ -e "$LEAN_PROJECT/.cache_fetched" ]; then
  ok "Mathlib's files fetched"
else
  repair_packages
  (cd "$LEAN_PROJECT" && lake exe cache get)
  touch "$LEAN_PROJECT/.cache_fetched"
fi
mkdir -p orchestration_workspace/.lake
[ -e orchestration_workspace/.lake/packages ] \
  || ln -s "$LEAN_PROJECT/.lake/packages" orchestration_workspace/.lake/packages
ok "orchestration_workspace/.lake/packages -> $(readlink -f orchestration_workspace/.lake/packages)"

if [ "$CLAUDE" = yes ]; then
  step "claude CLI"
  if command -v claude >/dev/null; then
    ok "$(claude --version 2>/dev/null | head -1)"
  else
    curl -fsSL https://claude.ai/install.sh | bash
  fi
  [ -f "$HOME/.claude/.credentials.json" ] && ok "logged in (~/.claude/.credentials.json)" \
    || echo "    run \`claude\` once and /login, or set ANTHROPIC_API_KEY"
fi

if [ "$CODEX" = yes ]; then
  step "codex CLI"
  if command -v codex >/dev/null; then
    ok "$(codex --version 2>/dev/null | head -1)"
  elif command -v npm >/dev/null; then
    npm install -g --prefix "$HOME/.local" @openai/codex
  else
    echo "    npm is needed (on a cluster, load a Node.js module first)" >&2
  fi
  echo "    run \`codex login\`, or set OPENAI_API_KEY"
fi

if [ "$SEARCH" = yes ]; then
  step "Paper search: virtualenv and arXiv index"
  UV_PROJECT_ENVIRONMENT="$ROOT/aiprover/venv_search" uv sync -q --frozen --python 3.12 --project nix/python/search
  aiprover/venv_search/bin/hf download LoganLuna/aiprover-arxiv-math-index --repo-type dataset \
    --local-dir data/arxiv_index
  ok "data/arxiv_index; start the service with scripts/systemd/install aiprover_paper_search"
fi

if [ -n "${AIPROVER_MODEL:-}" ] && [ ! -x "$VLLM_VENV/bin/vllm" ]; then
  step "vLLM virtualenv: $VLLM_VENV"
  uv venv -q --python 3.12 "$VLLM_VENV"
  VIRTUAL_ENV="$VLLM_VENV" uv pip install -q "vllm==$VLLM_VERSION"
fi

if [ "$BUILD" = yes ]; then
  step "CPU jobs: Lean builds, then packs"
  mkdir -p logs
  job() {  # job NAME CPUS MEM TIME DEPENDENCY COMMAND
    sbatch --parsable --job-name="aiprover_$1" --cpus-per-task="$2" --mem="$3" --time="$4" \
      ${5:+--dependency="afterok:$5"} --output="$ROOT/logs/%x_%j.out" --wrap "$6" | cut -d';' -f1
  }
  build=$(job build 8 32G 01:30:00 "" \
    "cd '$LEAN_PROJECT' && lake build repl && lake build cslib/Cslib && cd '$ROOT/orchestration_workspace' && echo 'import Mathlib' | lake env lean --stdin")
  packs=("$LEAN_PROJECT")
  if [ -f "${VLLM_VENV:-}/pyvenv.cfg" ]; then
    packs+=("$VLLM_VENV" "$(dirname "$(sed -n 's/^home *= *//p' "$VLLM_VENV/pyvenv.cfg")")")
  fi
  pack=$(job pack 16 16G 02:00:00 "$build" "scripts/slurm/pack.sh ${packs[*]@Q}")
  ok "build job $build, then pack job $pack"
fi
step "Next: once the jobs have finished (squeue --me), source scripts/slurm/env.sh and submit"
