# aiprover-captain plugin

A Claude Code skill that runs the orchestration method
(`aiprover_orchestration/orchestrator/`, `docs/orchestration.md`) with the
session's agent as captain. The session's subagents act as auditor, reviewer
and writer; lemmas are proved by AIProver jobs. The skill makes no
programmatic model calls.

The harness, every Lean check and the lean-lsp tools run on the machine of
the Claude Code session. Only AIProver model inference goes to the model
server, reached as the AIProver plugin reaches it (`AIProver/AIProver_plugin/README.md`).

## Setup

One command from a fresh clone, on Linux, WSL2 or macOS:

```bash
git clone --recurse-submodules https://github.com/Logan-Cole-Luna/aiprover-orchestration aiprover_orchestration && cd aiprover_orchestration && ./setup --endpoint http://<model host>:<port>
```

`./setup` (repository root) checks the platform and fetches the submodules.
It finds Nix, or offers to install it, asking first and showing the command.
It builds the environment from `flake.nix` and writes this directory's
`endpoint.toml` and `aiprover.toml` for the device. Finally it runs
`setup.sh lean claude endpoint doctor`.
`./setup --dry-run` prints every step without changing anything.

| Where the model server is | Command | Connection |
|---|---|---|
| none | `--endpoint none` | no model server: Claude subagents prove the lemmas; the doctor checks Lean only |
| reachable from the device | `--endpoint URL [--api-key KEY]` | direct; the key is sent as a bearer token |
| reachable only from an SSH host | `--endpoint URL --via HOST [--user USER]` | SSH tunnel through `HOST` (a host name or an alias of `~/.ssh/config`) |
| a scheduled job writing `node=<n> port=<p>` to a file on a login node | `--via HOST --handoff FILE` | SSH tunnel to the node the file names; a restarted job is followed |

The harness never talks to the server itself: it talks to a reasoning proxy
on the user's machine (`aiprover_orchestration/server/reasoning_proxy.py`,
port 18575), which forwards to the server directly or through the SSH
tunnel. The proxy turns tool calls that a server without a tool-call parser
returns as text into structured calls, caps reply length, repairs histories
the server would reject, and records each session's reasoning; so any
OpenAI-compatible server of the model works, with or without vLLM's tool
parser. `setup.sh endpoint` opens the connection and starts the proxy; the
skill runs it before each run.

```
harness -> reasoning proxy (127.0.0.1:18575) -> server            (direct)
harness -> reasoning proxy (127.0.0.1:18575) -> SSH tunnel -> server
```

The SSH routes authenticate once per login session: `setup.sh connect` opens
an SSH master connection and asks for what the host requires (a password, a
one-time code, a key passphrase). The AIProver CLI opens, checks and reopens
its tunnels over that master without prompting. When the master has closed
(reboot, sleep, network change), the doctor and the skill report it; rerun
`setup.sh connect`. `--timeout` and `--max-turns` set one AIProver session's
budget. Without `--endpoint`, `./setup` keeps an existing `endpoint.toml`, or
asks for one.

A run takes hours, so the connection must outlive network gaps. The SSH
master tolerates a gap or a sleep of up to 10 minutes; on macOS
`setup.sh endpoint` keeps the Mac awake while the proxy runs (`setup.sh
stop` ends both). When the master has dropped anyway, `setup.sh endpoint`,
which the skill runs after a connection fault, reopens it without a prompt
if a key, an agent or Kerberos authenticates; otherwise the user reruns it
in a terminal. After a login with a password, `setup.sh connect` offers to
create a key for this: a dedicated `~/.ssh/aiprover_ed25519`, installed on
the SSH host through the open connection and restricted there to port
forwarding to the model server (no shell; with a handoff file, reading that
file only). A host that requires a one-time code even with a key (an
`AuthenticationMethods` chain, as on TACC hosts) cannot reconnect
unattended; the key is then removed from it again, and the offer is not
repeated for that host.

| Built by Nix | Linked at | Content |
|---|---|---|
| `.#tools` | `aiprover/nix/tools` | Python 3.12 (pygments, aiohttp), jq, ripgrep, git, curl, ssh, GNU userland, flock, pdflatex |
| `.#venv-vibe` | `aiprover/nix/venv_vibe` | the harness's agent loop, mistral-vibe 2.24.2 (`nix/python/vibe/`) |
| `.#venv-mcp` | `aiprover/nix/venv_mcp` | lean-lsp-mcp 0.30.0 with AIProver's three fixes (`nix/python/mcp/`) |

The skill's `env.sh` puts `aiprover/nix/tools/bin` first on `PATH`, so a
Claude Code app started outside the Nix shell uses the same tools. The
virtualenvs pin exactly `AIProver/AIProver_plugin/setup/requirements-*.lock`.
`nix develop` (or `direnv allow`) gives the same tools interactively. Lean is
installed by elan into `~/.elan`, with Mathlib, the REPL and cslib in
`aiprover/lean_project` (`--lean-project PATH` reuses an existing project).

| `setup.sh` step | Effect |
|---|---|
| `lean` | elan, Lean 4.23.0, the Mathlib project with REPL and cslib (`AIProver/AIProver_plugin/setup.sh lean`); `orchestration_workspace/.lake/packages` linked to its packages |
| `claude` | links `~/.config/aiprover_orchestration/{root,aiprover.toml}`; installs the plugin from the local marketplace (without the `claude` CLI, prints the `/plugin` commands for the app) |
| `connect` | opens the SSH master of an SSH endpoint (asks for the host's credentials); nothing for a direct endpoint |
| `endpoint` | `connect`, the SSH tunnel (AIProver CLI, follows a handoff file) and the local reasoning proxy |
| `stop` | stops the reasoning proxy (and the keep-awake on macOS) |
| `doctor` | `aiprover doctor` (environment, model endpoint, the model thinks) and a structured tool-call check |
| `full` | `aiprover doctor --full`: as `doctor`, plus one live AIProver rollout |

`AIProver/AIProver_plugin/STARTUP.md` §5 maps each failing doctor row to its
fix. The skill runs `aiprover doctor` itself before every run.

## Use

Restart the Claude Code app (or VS Code). `/plugin` lists
`aiprover-orchestration`. Open the repository and ask for a problem to be run
with the aiprover-captain skill: by uuid of `data/*.jsonl`, or with an
informal statement and proof. `data/openai_math.jsonl` (five problems of the
OpenAI math release) is in git as a sample set, for example
`OpenAIMath_164_HindmanDistinctExpressions`; copy other problem files to a
new device. Results are written to `results/<run_id>/`.

To change the endpoint later, run `./setup --endpoint ... --no-lean` (connect
and doctor only), or edit `endpoint.toml` and run `setup.sh endpoint doctor`.

## Layout

| Path | Content |
|---|---|
| `endpoint.toml` | written by `./setup`: the connection to the model server and the local proxy (not in git) |
| `aiprover.toml` | written by `./setup`: the harness (through the proxy), runtime, paths (not in git) |
| `setup.sh` | the steps inside the environment: Lean, plugin, doctor |
| `claude_code/skills/aiprover-captain/SKILL.md` | the captain procedure |
| `…/references/` | Lean gates, subagent prompts, run record |
| `…/scripts/env.sh` | repository paths, tools on `PATH`, recording functions |
| `../flake.nix`, `../nix/` | the Nix environment: tools, virtualenvs, the lean-lsp-mcp patches |
