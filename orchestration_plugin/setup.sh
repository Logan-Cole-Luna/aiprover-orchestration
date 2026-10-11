#!/usr/bin/env bash
# =============================================================================
# aiprover-captain setup: provisions what the skill and its AIProver solvers need on THIS
# machine, installs the Claude Code plugin, then verifies. IDEMPOTENT: safe to re-run.
#
#   ./setup.sh            everything: deps, claude, doctor
#   ./setup.sh deps       elan + Lean 4.23.0, the Mathlib project (+ cslib), both venvs, ripgrep
#                         (AIProver/AIProver_plugin/setup.sh deps, with this directory's config)
#   ./setup.sh claude     link the config and the repository, (re)install the Claude Code plugin
#   ./setup.sh doctor     verify only (= aiprover doctor --full)
#
# Paths and the model endpoint come from aiprover.toml; edit that file first on a new machine.
# Rerun `claude` after editing the skill: an installed plugin runs from a cached copy that is
# refreshed only on reinstallation. Needs python3 >= 3.11, jq and the `claude` CLI.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(dirname "$HERE")"
CONFIG_DIR="$HOME/.config/aiprover_orchestration"
PLUGIN=aiprover-orchestration@aiprover-orchestration-local
export AIPROVER_CONFIG="$HERE/aiprover.toml"
AIP="$ROOT/AIProver/AIProver_plugin/bin/aiprover"

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf '   FAILED: %s\n' "$*" >&2; exit 1; }

step_deps() {
  say "deps (AIProver/AIProver_plugin/setup.sh deps, config $AIPROVER_CONFIG)"
  "$ROOT/AIProver/AIProver_plugin/setup.sh" deps
}

link_config() {
  # An INSTALLED plugin runs from a cache copy, which cannot find the repository or this
  # config by walking up; the skill's env.sh looks here.
  mkdir -p "$CONFIG_DIR"
  ln -sfn "$ROOT" "$CONFIG_DIR/root"
  ln -sfn "$HERE/aiprover.toml" "$CONFIG_DIR/aiprover.toml"
  ok "$CONFIG_DIR/root -> $ROOT"
  ok "$CONFIG_DIR/aiprover.toml -> $HERE/aiprover.toml"
}

step_claude() {
  say "Claude Code: plugin '$PLUGIN' (skill aiprover-captain)"
  command -v claude >/dev/null || die "claude CLI not on PATH"
  command -v jq >/dev/null || die "jq not on PATH (the skill records its trace with it)"
  link_config
  claude plugin marketplace add "$HERE" >/dev/null 2>&1 \
    || claude plugin marketplace update aiprover-orchestration-local >/dev/null 2>&1 || true
  claude plugin uninstall "$PLUGIN" >/dev/null 2>&1 || true
  claude plugin install "$PLUGIN" --scope user >/dev/null
  claude plugin list 2>/dev/null | grep -q "$PLUGIN" || die "plugin install failed"
  ok "installed; restart open Claude Code sessions to load it"
  claude plugin list 2>/dev/null | grep -q 'aiprover@aiprover-local' \
    || echo "   note: the lean-lsp MCP tools come with the AIProver plugin (AIProver/AIProver_plugin/setup.sh claude)"
}

step_doctor() {
  # Not --agents: that check asks the agents through `claude -p`, which the skill avoids.
  say "doctor --full"
  "$AIP" doctor --full
}

# ---------------------------------------------------------------------------
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "python3 >= 3.11 required"
[ $# -eq 0 ] && set -- all
for step in "$@"; do
  case "$step" in
    all)    step_deps; step_claude; step_doctor ;;
    deps)   step_deps ;;
    claude) step_claude ;;
    doctor) step_doctor ;;
    *) die "unknown step '$step' (all|deps|claude|doctor)" ;;
  esac
done
