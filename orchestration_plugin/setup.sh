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
#   ./setup.sh endpoint connect, open the SSH tunnel, start the local reasoning proxy (on macOS
#                       also keeps the Mac awake while it runs); the skill runs it before a
#                       run and after a connection fault
#   ./setup.sh stop     stop the reasoning proxy (and the keep-awake) after the runs
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
  [ -f "$ENDPOINT_CONFIG" ] || die "no endpoint.toml: run ./setup --endpoint ... first"
  load_ssh_endpoint
  if [ -z "$sock" ]; then
    ok "none needed (direct endpoint, or SSH without a control socket)"
    return 0
  fi
  if ssh -S "$sock" -O check "$dest" 2>/dev/null; then
    ok "open ($dest, $sock)"
    return 0
  fi
  # The master survives a network gap or a sleep of up to 10 min (ServerAlive 30 s x 20).
  local open=(ssh -fNM -S "$sock" -p "$port" ${opts[@]+"${opts[@]}"} -o ServerAliveInterval=30 -o ServerAliveCountMax=20 "$dest")
  mkdir -p "$(dirname "$sock")"
  # Without a prompt first: succeeds when a key, an agent or Kerberos authenticates, so a
  # dropped master is reopened without the user.
  if "${open[@]:0:2}" -o BatchMode=yes -o ConnectTimeout=20 "${open[@]:2}" 2>/dev/null \
      && ssh -S "$sock" -O check "$dest" 2>/dev/null; then
    ok "reopened without a prompt ($dest, $sock)"
    return 0
  fi
  if [ ! -t 0 ]; then
    echo "   not open; run in a terminal (it asks for your credentials): ${open[*]}"
    return 1
  fi
  echo "   $dest asks for your credentials (for example a password and a one-time code)"
  "${open[@]}" && ssh -S "$sock" -O check "$dest" 2>/dev/null \
    || die "could not open the SSH connection to $dest"
  ok "open ($dest, $sock)"
  offer_key
}

# The SSH side of endpoint.toml as shell variables: control socket, port, destination, the
# [endpoint].ssh_options array, and the forwarding target (remote host and port, handoff file).
load_ssh_endpoint() {
  eval "$(python3 - "$ENDPOINT_CONFIG" <<'PYEOF'
import os, shlex, sys, tomllib
endpoint = tomllib.load(open(sys.argv[1], "rb")).get("endpoint", {})
ssh = endpoint.get("mode", "ssh") == "ssh" and endpoint.get("control_socket")
user, host = endpoint.get("ssh_user", ""), endpoint.get("ssh_host", "")
values = {
    "sock": os.path.expanduser(endpoint["control_socket"]) if ssh else "",
    "port": endpoint.get("ssh_port", 22),
    "dest": f"{user}@{host}" if user else host,
    "remote": f"{endpoint.get('remote_host', '')}:{endpoint.get('remote_port', '')}",
    "handoff": endpoint.get("server_handoff", ""),
}
for name, value in values.items():
    print(f"{name}={shlex.quote(str(value))}")
options = [os.path.expanduser(str(o)) for o in endpoint.get("ssh_options", [])]
print("opts=(" + " ".join(shlex.quote(o) for o in options) + ")")
PYEOF
  )"
}

# After a login with credentials, offers a key so that a dropped master reopens without the
# user. The key is dedicated (~/.ssh/aiprover_ed25519, no passphrase, needed unattended) and
# restricted on the host: forwarding to the model server only, no shell (with a handoff file,
# reading that file only). A host that demands a one-time code even with a key (an
# AuthenticationMethods chain, as at TACC) gains nothing: the key is removed from it again and
# the offer is not repeated for that host.
KEY="$HOME/.ssh/aiprover_ed25519"
offer_key() {
  local declined="$CONFIG_DIR/no-key/$dest"
  [ -t 0 ] && [ ! -f "$declined" ] || return 0
  ssh -S none -o BatchMode=yes -o ConnectTimeout=20 -p "$port" ${opts[@]+"${opts[@]}"} "$dest" true \
    >/dev/null 2>&1 && return 0
  mkdir -p "$(dirname "$declined")"
  printf '   Create an SSH key so that this connection reopens without your credentials? [Y/n] '
  local answer
  read -r answer || answer=n
  case "$answer" in
  [Nn]*) echo "declined" > "$declined"; return 0 ;;
  esac
  [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "aiprover-orchestration $(whoami)@$(hostname -s)" -f "$KEY"
  local restriction line
  if [ -n "$handoff" ]; then
    restriction="restrict,port-forwarding,command=\"cat $handoff\""
  else
    restriction="restrict,port-forwarding,permitopen=\"$remote\",command=\"echo aiprover: port forwarding only\""
  fi
  line="$restriction $(cat "$KEY.pub")"
  printf '%s\n' "$line" | ssh -S "$sock" "$dest" \
    'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; k=$(cat); grep -qxF "$k" ~/.ssh/authorized_keys || printf "%s\n" "$k" >> ~/.ssh/authorized_keys' \
    || die "could not install the key on $dest"
  local trial
  trial=$(ssh -v -S none -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes -i "$KEY" \
    -p "$port" ${opts[@]+"${opts[@]}"} "$dest" true 2>&1) && {
    set_ssh_options "$KEY"
    ok "key installed on $dest ($KEY, forwarding only); the connection now reopens by itself"
    return 0
  }
  printf '%s\n' "$line" | ssh -S "$sock" "$dest" \
    'umask 077; k=$(cat); grep -vxF "$k" ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.tmp; mv ~/.ssh/authorized_keys.tmp ~/.ssh/authorized_keys' || true
  if printf '%s' "$trial" | grep -q "partial success"; then
    echo "one-time code required" > "$declined"
    echo "   $dest requires a one-time code even with a key, so a key cannot reopen the"
    echo "   connection by itself; the key was removed from it again. A dropped connection"
    echo "   needs setup.sh connect in a terminal."
  else
    echo "   $dest did not accept the key; it was removed from it again"
  fi
}

# Writes [endpoint].ssh_options of endpoint.toml to use the key.
set_ssh_options() {
  python3 - "$ENDPOINT_CONFIG" "$1" <<'PYEOF'
import json, re, sys
path, key = sys.argv[1:]
text = open(path).read()
options = json.dumps(["-i", key, "-o", "IdentitiesOnly=yes"])
text = re.sub(r"(?m)^ssh_options = .*$", f"ssh_options = {options}", text, count=1)
open(path, "w").write(text)
PYEOF
}

# The tunnel port belongs to this endpoint. An ssh process holding it other than the current
# master is a forward left by an earlier master (an earlier control socket, or a master whose
# connection went stale): it accepts connections it can no longer carry, and a new forward
# cannot bind the port. It is closed before the tunnel is checked.
close_stale_forward() {
  local port=$1 socket=$2 dest=$3 holder master command
  holder=$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true)
  [ -n "$holder" ] || return 0
  master=$(ssh -S "$socket" -O check "$dest" 2>&1 | sed -n 's/.*pid=\([0-9]*\).*/\1/p')
  [ "$holder" != "$master" ] || return 0
  command=$(ps -o comm= -p "$holder" 2>/dev/null || true)
  case "$command" in
  *ssh)
    kill "$holder" && sleep 1
    ok "closed a stale forward on port $port (ssh pid $holder, not the current master)"
    ;;
  *) die "port $port is held by $command (pid $holder): free it or change [endpoint].local_port" ;;
  esac
}

# The model server as the harness reaches it: SSH master and tunnel (ssh mode, opened and
# followed through a handoff file by the AIProver CLI), then the local reasoning proxy in front
# of it. The proxy runs detached; it is restarted when its upstream has changed.
step_endpoint() {
  step_connect
  say "Tunnel and reasoning proxy"
  local mode listen upstream max_reply_tokens tunnel_port socket dest
  eval "$(python3 - "$ENDPOINT_CONFIG" <<'PYEOF'
import os, shlex, sys, tomllib
config = tomllib.load(open(sys.argv[1], "rb"))
endpoint, proxy = config["endpoint"], config["proxy"]
user, host = endpoint.get("ssh_user", ""), endpoint.get("ssh_host", "")
for name, value in (("mode", endpoint.get("mode", "ssh")),
                    ("listen", proxy["listen"]), ("upstream", proxy["upstream"]),
                    ("max_reply_tokens", proxy.get("max_reply_tokens", 16384)),
                    ("tunnel_port", endpoint.get("local_port", 18555)),
                    ("socket", os.path.expanduser(endpoint.get("control_socket", ""))),
                    ("dest", f"{user}@{host}" if user else host)):
    print(f"{name}={shlex.quote(str(value))}")
PYEOF
  )"
  if [ "$mode" = ssh ]; then
    close_stale_forward "$tunnel_port" "$socket" "$dest"
    AIPROVER_CONFIG="$ENDPOINT_CONFIG" "$AIP" tunnel status >/dev/null 2>&1 \
      || AIPROVER_CONFIG="$ENDPOINT_CONFIG" "$AIP" tunnel up \
      || die "tunnel failed (see above); after setup.sh connect, run setup.sh endpoint again"
    ok "tunnel $upstream"
  fi
  local state="$ROOT/aiprover/work/proxy.json" running
  running=$(curl -s -m 5 "http://127.0.0.1:$listen/upstreams" | jq -r '.upstreams[0].url // empty' 2>/dev/null || true)
  if [ "$running" = "$upstream" ]; then
    ok "reasoning proxy :$listen -> $upstream"
    check_path "$listen" "$upstream"
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
# On macOS, idle sleep would cut the SSH connection under a run: caffeinate keeps the Mac
# awake while the proxy lives (it exits with it).
if sys.platform == "darwin":
    subprocess.Popen(["caffeinate", "-ims", "-w", str(process.pid)],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
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
  check_path "$listen" "$upstream"
}

# The whole path, harness side: the proxy must reach the server. A forward left by an earlier
# SSH master (another control socket) can hold the tunnel port and accept connections it can
# no longer carry; then name the listener, so it can be closed.
check_path() {
  local listen=$1 upstream=$2 port
  if curl -s -m 30 -f -o /dev/null "http://127.0.0.1:$listen/v1/models"; then
    ok "model server answers through the proxy"
    return 0
  fi
  echo "   the proxy does not reach $upstream (log logs/reasoning_proxy_skill.log)"
  port=${upstream##*:}
  case "$upstream" in
  http://127.0.0.1:*)
    local listener
    listener=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -z "$listener" ]; then
      echo "   nothing listens on the tunnel port $port: the tunnel is down; rerun setup.sh endpoint"
    else
      echo "   listener on the tunnel port $port:"
      printf '%s\n' "$listener" | sed 's/^/     /'
      echo "   an ssh process of an earlier control socket holds a stale forward: close it"
      echo "   (ssh -S <socket> -O exit <host>, or kill the pid), then rerun setup.sh endpoint"
    fi
    ;;
  esac
  return 1
}

# Ends the reasoning proxy; on macOS its keep-awake ends with it. The SSH master stays open.
step_stop() {
  say "Reasoning proxy"
  local state="$ROOT/aiprover/work/proxy.json"
  if [ -f "$state" ] && kill "$(jq -r .pid "$state")" 2>/dev/null; then
    rm -f "$state"
    ok "stopped"
  else
    ok "not running"
  fi
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
    stop) step_stop ;;
    doctor) step_doctor ;;
    full)   step_doctor --full ;;
    *) die "unknown step '$step' (lean|claude|connect|endpoint|stop|doctor|full)" ;;
  esac
done
