#!/usr/bin/env bash
# Builds what ArXivLean runs and their grading need, once per machine:
#   1. the orchestration's Lean release v4.34.1 (Mathlib d13f23b, MathArena's pin);
#   2. MathArena at the revision of the September 2026 edition;
#   3. Go, if missing, and 4. Landrun, the comparator's sandbox;
#   5. a small Python environment with the packages the grader imports;
#   6. MathArena's comparator runtime (its own Lean project and binaries).
# Each step is skipped when its result exists. It needs Linux with Landlock,
# Python 3.12 or later, elan, git, curl, and about 30 GB of disk; it ends by
# checking one statement with the comparator.
#
#   bash benchmark/arxivlean_setup.sh
#   PYTHON=~/lean_env/bin/python3 EXTERNAL=/data/external bash benchmark/arxivlean_setup.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
EXTERNAL="${EXTERNAL:-$HOME/workspace/external}"
MATHARENA_DIR="${MATHARENA_DIR:-$EXTERNAL/matharena}"
RUNTIME="${MATHARENA_LEAN_RUNTIME_ROOT:-$EXTERNAL/matharena_runtime}"
MATHARENA_REV=cec19dfe83e2881a8bfcf640f16d0d29718b033a
LANDRUN_REV=811cfff51ceaf3d9843708aa6d22e9b84ccac8b4
export PATH="$HOME/.elan/bin:$PATH"

echo "1. Lean release v4.34.1"
if [ ! -e "$ROOT/lean_releases/v4.34.1/ready" ]; then
  (cd "$ROOT" && "$PYTHON" -m aiprover_orchestration.lean.releases v4.34.1)
fi

echo "2. MathArena at $MATHARENA_REV"
if [ ! -d "$MATHARENA_DIR/.git" ]; then
  git clone -q https://github.com/eth-sri/matharena.git "$MATHARENA_DIR"
fi
git -C "$MATHARENA_DIR" fetch -q --depth 1 origin "$MATHARENA_REV"
git -C "$MATHARENA_DIR" checkout -q --detach "$MATHARENA_REV"

echo "3. Go"
if ! command -v go >/dev/null; then
  if [ ! -x "$HOME/.local/go/bin/go" ]; then
    arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
    version=$(curl -fsS 'https://go.dev/VERSION?m=text' | head -1)
    mkdir -p "$HOME/.local"
    curl -fsSL "https://go.dev/dl/$version.linux-$arch.tar.gz" | tar -xz -C "$HOME/.local"
  fi
  export PATH="$HOME/.local/go/bin:$PATH"
fi

echo "4. Landrun at $LANDRUN_REV"
LANDRUN="$MATHARENA_DIR/external/landrun/bin/landrun"
if [ ! -x "$LANDRUN" ]; then
  source_dir="$MATHARENA_DIR/external/landrun/src"
  [ -d "$source_dir" ] || git clone -q https://github.com/Zouuup/landrun.git "$source_dir"
  git -C "$source_dir" checkout -q --detach "$LANDRUN_REV"
  (cd "$source_dir" && go build -o ../bin/landrun ./cmd/landrun)
fi

echo "5. Grading environment"
VENV="$MATHARENA_DIR/.venv-grade"
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" install -q "axiom-axle>=1.1.0" sympy loguru
fi

echo "6. Comparator runtime in $RUNTIME"
if [ ! -e "$RUNTIME/runtime.json" ]; then
  (cd "$MATHARENA_DIR" && PYTHONPATH=src "$VENV/bin/python" \
    arxivmath/scripts/lean/setup_conjecture_runtime.py \
    --runtime-root "$RUNTIME" --elan "$HOME/.elan/bin/elan" --landrun-bin "$LANDRUN")
fi

echo "Check: the comparator on the first statement"
head -1 "$ROOT/benchmark/arxivlean/arxivlean_0926.jsonl" \
  | MATHARENA_LEAN_RUNTIME_ROOT="$RUNTIME" PYTHONPATH="$MATHARENA_DIR/src" "$VENV/bin/python" -c '
import json, sys
from matharena.lean_conjecture import check_statement
formal = json.loads(sys.stdin.read())["formal_statement"]
formal = formal[: formal.rindex("theorem arxivlean ")].rstrip() + "\n"
status = check_statement(formal)["status"]
print(status)
sys.exit(status != "verified")'
echo "ready"
