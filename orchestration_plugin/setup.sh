#!/usr/bin/env bash
# =============================================================================
# aiprover-captain setup steps inside the environment. ./setup at the repository root runs them
# after building the Nix tools and virtualenvs and writing endpoint.toml (the connection to the
# model server) and aiprover.toml (the harness, which talks to the local reasoning proxy); each
# step is idempotent and can be rerun on its own.
#
#   ./setup.sh lean     elan + Lean 4.23.0, the Mathlib project (+ REPL, cslib) at
#                       [paths].lean_project (AIProver/AIProver_plugin/setup.sh lean);
#                       orchestration_workspace/.lake/packages linked to its packages
#   ./setup.sh claude   link the config and the repository; (re)install the Claude Code plugin
#   ./setup.sh connect  open the SSH master of an ssh-mode endpoint (asks for the host's
#                       credentials once); rerun when it has closed
#   ./setup.sh endpoint connect, open the SSH tunnel, start the local reasoning proxy; the
#                       skill runs it before a run and after a connection fault
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
ENDPOINT_CONFIG="$HERE/endpoint.toml"
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
# host requires (a password, a one-time code, a key passphrase); the AIProver CLI's tunnels ride it
# without prompting. Rerun when it has closed (reboot, sleep, network change).
step_connect() {
  say "SSH connection of the model endpoint"
  local spec sock port dest
  [ -f "$ENDPOINT_CONFIG" ] || die "no endpoint.toml: run ./setup --endpoint ... first"
  spec=$(python3 - "$ENDPOINT_CONFIG" <<'PYEOF'
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
    return 1
  fi
  mkdir -p "$(dirname "$sock")"
  echo "   $dest asks for your credentials (for example a password and a one-time code)"
  "${open[@]}" && ssh -S "$sock" -O check "$dest" 2>/dev/null \
    || die "could not open the SSH connection to $dest"
  ok "open ($dest, $sock)"
}

# The model server as the harness reaches it: SSH master and tunnel (ssh mode, opened and
# followed through a handoff file by the AIProver CLI), then the local reasoning proxy in front
# of it. The proxy runs detached; it is restarted when its upstream has changed.
step_endpoint() {
  step_connect
  say "Tunnel and reasoning proxy"
  local mode listen upstream max_reply_tokens
  eval "$(python3 - "$ENDPOINT_CONFIG" <<'PYEOF'
import shlex, sys, tomllib
config = tomllib.load(open(sys.argv[1], "rb"))
proxy = config["proxy"]
for name, value in (("mode", config["endpoint"].get("mode", "ssh")),
                    ("listen", proxy["listen"]), ("upstream", proxy["upstream"]),
                    ("max_reply_tokens", proxy.get("max_reply_tokens", 16384))):
    print(f"{name}={shlex.quote(str(value))}")
PYEOF
  )"
  if [ "$mode" = ssh ]; then
    AIPROVER_CONFIG="$ENDPOINT_CONFIG" "$AIP" tunnel status >/dev/null 2>&1 \
      || AIPROVER_CONFIG="$ENDPOINT_CONFIG" "$AIP" tunnel up \
      || die "tunnel failed (see above); after setup.sh connect, run setup.sh endpoint again"
    ok "tunnel $upstream"
  fi
  local state="$ROOT/aiprover/work/proxy.json" running
  running=$(curl -s -m 5 "http://127.0.0.1:$listen/upstreams" | jq -r '.upstreams[0].url // empty' 2>/dev/null || true)
  if [ "$running" = "$upstream" ]; then
    ok "reasoning proxy :$listen -> $upstream"
    return 0
  fi
  if [ -n "$running" ] && [ -f "$state" ]; then
    kill "$(jq -r .pid "$state")" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      curl -s -m 1 -o /dev/null "http://127.0.0.1:$listen/upstreams" || break
      sleep 1
    done
    running=$(curl -s -m 5 "http://127.0.0.1:$listen/upstreams" | jq -r '.upstreams[0].url // empty' 2>/dev/null || true)
  fi
  [ -z "$running" ] || die "port $listen is held by another proxy ($running): free it or change [proxy].listen"
  mkdir -p "$ROOT/logs" "$ROOT/aiprover/work"
  (cd "$ROOT" && python3 - "$listen" "$upstream" "$max_reply_tokens" "$state" <<'PYEOF'
import json, subprocess, sys
listen, upstream, max_reply_tokens, state = sys.argv[1:]
log = open("logs/reasoning_proxy_skill.log", "a")
process = subprocess.Popen(
    [sys.executable, "-m", "aiprover_orchestration.server.reasoning_proxy",
     "--listen", listen, "--upstream", upstream, "--max-reply-tokens", max_reply_tokens],
    stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
json.dump({"pid": process.pid, "listen": int(listen), "upstream": upstream}, open(state, "w"))
PYEOF
  )
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -s -m 2 -o /dev/null "http://127.0.0.1:$listen/upstreams" && break
    sleep 1
  done
  curl -s -m 5 -o /dev/null "http://127.0.0.1:$listen/upstreams" \
    || die "the reasoning proxy did not start: see logs/reasoning_proxy_skill.log"
  ok "reasoning proxy :$listen -> $upstream (log logs/reasoning_proxy_skill.log)"
}

# The harness acts only through structured tool calls. A vLLM server started without a
# tool-call parser returns them as text ([TOOL_CALLS]name[ARGS]{...}), which the reasoning proxy
# converts; without structured calls every session ends without an answer (status `empty`).
check_tool_calls() {
  local api
  api=$("$AIP" config --shell | sed -n 's/^AIP_API_BASE=//p')
  python3 - "$api" <<'PYEOF'
import json, sys, urllib.request
api = sys.argv[1]
try:
    models = json.load(urllib.request.urlopen(f"{api}/models", timeout=30))
    body = {"model": models["data"][0]["id"], "max_tokens": 400,
            "messages": [{"role": "user", "content": "Use the tool to read the file Work.lean."}],
            "tools": [{"type": "function", "function": {
                "name": "read_file", "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                               "required": ["path"]}}}]}
    request = urllib.request.Request(f"{api}/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    message = json.load(urllib.request.urlopen(request, timeout=300))["choices"][0]["message"]
except Exception as error:
    print(f"  FAIL  structured tool calls             no answer from {api}: {error}")
    sys.exit(0)
if message.get("tool_calls"):
    print("  PASS  structured tool calls             the endpoint returns tool_calls")
elif "[TOOL_CALLS]" in (message.get("content") or ""):
    print("  FAIL  structured tool calls             tool calls arrive as text: the server has no "
          "tool-call parser; connect through the reasoning proxy (or start vLLM with "
          "--enable-auto-tool-choice --tool-call-parser mistral)")
else:
    print("  FAIL  structured tool calls             the model answered without calling the tool")
PYEOF
}

# Not --agents: that check asks the agents through `claude -p`, which the skill avoids.
step_doctor() {
  say "doctor $*"
  "$AIP" doctor "$@" || echo "   some rows failed: the skill's preflight (SKILL.md step 0) lists the rows a run needs"
  check_tool_calls
  "$AIP" slots >/dev/null 2>&1 \
    || echo "   note: this AIProver CLI has no 'slots'/'grow': jobs then keep the sessions they start with"
}

# ---------------------------------------------------------------------------
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "python3 >= 3.11 required"
[ $# -eq 0 ] && set -- lean claude endpoint doctor
for step in "$@"; do
  case "$step" in
    lean)   step_lean ;;
    claude) step_claude ;;
    connect) step_connect ;;
    endpoint) step_endpoint ;;
    doctor) step_doctor ;;
    full)   step_doctor --full ;;
    *) die "unknown step '$step' (lean|claude|connect|endpoint|doctor|full)" ;;
  esac
done
