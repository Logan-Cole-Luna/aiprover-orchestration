#!/usr/bin/env bash
# =============================================================================
# aiprover-captain setup steps inside the environment. ./setup at the repository root runs them
# after building the Nix tools and virtualenvs and writing aiprover.toml; each step is
# idempotent and can be rerun on its own.
#
#   ./setup.sh lean     elan + Lean 4.23.0, the Mathlib project (+ REPL, cslib) at
#                       [paths].lean_project (AIProver/AIProver_plugin/setup.sh lean);
#                       orchestration_workspace/.lake/packages linked to its packages
#   ./setup.sh claude   link the config and the repository; (re)install the Claude Code plugin
#   ./setup.sh connect  open the SSH master of an ssh-mode endpoint (asks for the password
#                       and TACC_Token at TACC); rerun when it has closed
#   ./setup.sh doctor   verify the environment and the model endpoint (aiprover doctor)
#   ./setup.sh full     as doctor, plus one live AIProver rollout (aiprover doctor --full)
#
# Rerun `claude` after editing the skill: an installed plugin runs from a cached copy that is
# refreshed only on reinstallation.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(dirname "$HERE")"
CONFIG_DIR="$HOME/.config/aiprover_orchestration"
PLUGIN=aiprover-orchestration@aiprover-orchestration-local
MARKETPLACE=aiprover-orchestration-local
export AIPROVER_CONFIG="$HERE/aiprover.toml"
AIP="$ROOT/AIProver/AIProver_plugin/bin/aiprover"
[ -d "$ROOT/aiprover/nix/tools/bin" ] && export PATH="$ROOT/aiprover/nix/tools/bin:$PATH"

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf '   FAILED: %s\n' "$*" >&2; exit 1; }

step_lean() {
  say "Lean (AIProver/AIProver_plugin/setup.sh lean, config $AIPROVER_CONFIG)"
  "$ROOT/AIProver/AIProver_plugin/setup.sh" lean
  # orchestration_workspace/ pins the same Mathlib and cslib; the module statement check
  # builds against the packages already built in the AIProver Lean project.
  local packages
  packages="$("$AIP" config --shell | sed -n 's/^AIP_LEAN_PROJECT=//p')/.lake/packages"
  mkdir -p "$ROOT/orchestration_workspace/.lake"
  [ -e "$ROOT/orchestration_workspace/.lake/packages" ] \
    || ln -s "$packages" "$ROOT/orchestration_workspace/.lake/packages"
  ok "orchestration_workspace/.lake/packages -> $(readlink -f "$ROOT/orchestration_workspace/.lake/packages")"
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
  [ -f "$HERE/aiprover.toml" ] || die "no aiprover.toml: run ./setup --endpoint ... first"
  link_config
  if ! command -v claude >/dev/null; then
    echo "   the claude CLI is not on PATH; install the plugin from the Claude Code app instead:"
    echo "     /plugin marketplace add $HERE"
    echo "     /plugin install $PLUGIN"
    return 0
  fi
  claude plugin marketplace add "$HERE" >/dev/null 2>&1 \
    || claude plugin marketplace update "$MARKETPLACE" >/dev/null 2>&1 || true
  claude plugin uninstall "$PLUGIN" >/dev/null 2>&1 || true
  claude plugin install "$PLUGIN" --scope user >/dev/null
  claude plugin list 2>/dev/null | grep -q "$PLUGIN" || die "plugin install failed"
  ok "installed; restart open Claude Code sessions to load it"
  claude plugin list 2>/dev/null | grep -q 'aiprover@aiprover-local' \
    || echo "   note: the lean-lsp MCP tools come with the AIProver plugin (AIProver/AIProver_plugin/setup.sh claude)"
}

# Opens the SSH master of an ssh-mode endpoint, once per login session. It asks for what the
# host requires (at TACC a password, then TACC_Token); the AIProver CLI's tunnels then ride it
# without prompting. Rerun when it has closed (reboot, sleep, network change).
step_connect() {
  say "SSH connection of the model endpoint"
  local spec sock port dest
  spec=$(python3 - "$AIPROVER_CONFIG" <<'PYEOF'
import os, sys, tomllib
endpoint = tomllib.load(open(sys.argv[1], "rb")).get("endpoint", {})
if endpoint.get("mode", "ssh") == "ssh" and endpoint.get("control_socket"):
    user = endpoint.get("ssh_user", "")
    host = endpoint["ssh_host"]
    print(os.path.expanduser(endpoint["control_socket"]), endpoint.get("ssh_port", 22),
          f"{user}@{host}" if user else host)
PYEOF
  )
  if [ -z "$spec" ]; then
    ok "none needed (direct endpoint, or SSH without a control socket)"
    return 0
  fi
  read -r sock port dest <<< "$spec"
  if ssh -S "$sock" -O check "$dest" 2>/dev/null; then
    ok "open ($dest, $sock)"
    return 0
  fi
  local open=(ssh -fNM -S "$sock" -p "$port" -o ServerAliveInterval=30 -o ServerAliveCountMax=3 "$dest")
  if [ ! -t 0 ]; then
    echo "   not open; run in a terminal (it asks for your credentials): ${open[*]}"
    return 0
  fi
  mkdir -p "$(dirname "$sock")"
  echo "   $dest asks for your credentials (TACC: password, then TACC_Token)"
  "${open[@]}" && ssh -S "$sock" -O check "$dest" 2>/dev/null \
    || die "could not open the SSH connection to $dest"
  ok "open ($dest, $sock)"
}

# Not --agents: that check asks the agents through `claude -p`, which the skill avoids.
step_doctor() {
  say "doctor $*"
  "$AIP" doctor "$@" || echo "   some rows failed: the skill's preflight (SKILL.md step 0) lists the rows a run needs"
  "$AIP" slots >/dev/null 2>&1 \
    || echo "   note: this AIProver CLI has no 'slots'/'grow': jobs then keep the sessions they start with"
}

# ---------------------------------------------------------------------------
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "python3 >= 3.11 required"
[ $# -eq 0 ] && set -- lean claude connect doctor
for step in "$@"; do
  case "$step" in
    lean)   step_lean ;;
    claude) step_claude ;;
    connect) step_connect ;;
    doctor) step_doctor ;;
    full)   step_doctor --full ;;
    *) die "unknown step '$step' (lean|claude|connect|doctor|full)" ;;
  esac
done
