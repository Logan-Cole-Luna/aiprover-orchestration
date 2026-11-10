#!/bin/bash
# Set up this checkout on a Slurm cluster without Nix, where ./setup cannot
# create /nix. Run it from the checkout on a login node: it performs the steps
# that need the network, which compute nodes lack, and submits the builds as CPU
# jobs, since a login node runs nothing heavy.
#
#   bash scripts/slurm/setup.sh [--claude] [--no-build]
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
#   5. with --claude, the claude CLI, whose login in ~/.claude compute nodes share
#   6. with AIPROVER_MODEL, the virtualenv VLLM_VENV with vLLM, unless it exists
#   7. CPU jobs: the Lean builds, then the packs of scripts/slurm/pack.sh (the
#      Lean project, and the vLLM virtualenv VLLM_VENV with its Python, if set)
#
# configs/aiprover/slurm.toml names these paths. The account is SBATCH_ACCOUNT.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
. "$ROOT/scripts/slurm/env.sh"
CLAUDE=no
BUILD=yes
while [ $# -gt 0 ]; do
  case "$1" in
    --claude) CLAUDE=yes ;;
    --no-build) BUILD=no ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
LEAN_TOOLCHAIN="leanprover/lean4:v4.23.0"
LEAN_PROJECT="$AIPROVER_LEAN_PROJECT"
RG_VERSION=14.1.1
export PATH="$HOME/.elan/bin:$HOME/.local/bin:$PATH"
# Login nodes cap a user's threads; uv sizes its pools by the node's cores
# and panics ("failed to initialize global rayon pool") without these.
export RAYON_NUM_THREADS="${RAYON_NUM_THREADS:-4}" UV_CONCURRENT_INSTALLS="${UV_CONCURRENT_INSTALLS:-4}" \
       UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-8}" UV_CONCURRENT_BUILDS="${UV_CONCURRENT_BUILDS:-2}"
step() { printf '\n==> %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
command -v uv >/dev/null || { echo "uv is needed: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

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
if [ ! -x "$HOME/.elan/bin/elan" ]; then
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none --no-modify-path
fi
elan toolchain install "$LEAN_TOOLCHAIN" >/dev/null 2>&1 || true
mkdir -p "$LEAN_PROJECT"
for f in lakefile.lean lake-manifest.json lean-toolchain; do
  [ -f "$LEAN_PROJECT/$f" ] || cp "AIProver/AIProver_plugin/setup/lean_project/$f" "$LEAN_PROJECT/$f"
done
[ -f "$LEAN_PROJECT/TmpProjDir.lean" ] || echo "-- library root (intentionally empty)" > "$LEAN_PROJECT/TmpProjDir.lean"
# Fetching Mathlib's prebuilt files hashes every file of the project, which is
# slow on a shared file system, so it runs once; the marker records that it did.
if [ -e "$LEAN_PROJECT/.cache_fetched" ]; then
  ok "Mathlib's files fetched"
else
  (cd "$LEAN_PROJECT" && lake exe cache get) && touch "$LEAN_PROJECT/.cache_fetched"
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

if [ -n "${AIPROVER_MODEL:-}" ] && [ ! -x "$VLLM_VENV/bin/vllm" ]; then
  step "vLLM virtualenv: $VLLM_VENV"
  uv venv -q --python 3.12 "$VLLM_VENV"
  VIRTUAL_ENV="$VLLM_VENV" uv pip install -q vllm
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
