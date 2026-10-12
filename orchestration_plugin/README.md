# aiprover-captain plugin

A Claude Code skill that runs the orchestration method
(`aiprover_orchestration/orchestrator/`, `docs/orchestration.md`) with the
session's agent as captain. The session's subagents act as auditor, reviewer
and writer; lemmas are proved by AIProver jobs. The skill makes no
programmatic model calls.

The harness, every Lean check and the lean-lsp tools run on the machine of
the Claude Code session. Only AIProver model inference goes to the model
server, reached as the AIProver plugin reaches it (`AIProver/AIProver_plugin/README.md`).

## Quick start

You supply **where the model is**: a host to SSH into and the model's
address as seen from it. Everything else is set up and checked by
`setup.sh`.

**0. Model server.** Use a running server, or start one with
`AIProver/AIProver_plugin/serve/serve_aiprover.sh` (any node with enough GPU
memory for the ~112 GB FP8 checkpoint) or
`scripts/vista/serve_aiprover_vista.sbatch` (two Vista GH200 nodes). The
Vista job writes its node and port to a handoff file on `$SCRATCH`.

**1. Edit `aiprover.toml`, `[endpoint]`.**

```toml
mode           = "ssh"
ssh_host       = "vista.tacc.utexas.edu"   # the host you SSH into
ssh_user       = "<your TACC user>"
server_handoff = "/scratch/11757/loganluna/servers/aiprover_server.txt"   # follows the server if it moves
remote_host    = ""                        # fixed address when there is no handoff file
remote_port    = 8060
control_socket = "~/.ssh/vista.sock"       # TACC needs MFA: see step 2; empty for key-based ssh
```

The compute node's port is not reachable from outside TACC, hence the SSH
hop; the tunnel is opened, checked and reopened by the AIProver CLI. For a
server reachable directly, set `mode = "direct"` and `api_base`
(`api_key` is sent as a bearer token). On the orchestration VM,
`api_base = "http://127.0.0.1:18565/v1"` uses the reasoning proxy, which
records the model's reasoning per session.

**2. TACC only: open one SSH master by hand** (MFA cannot be scripted):

```bash
ssh -fNM -S ~/.ssh/vista.sock -o ServerAliveInterval=30 <user>@vista.tacc.utexas.edu
```

**3. Set up and verify:**

```bash
./setup.sh
```

| Step | Effect |
|---|---|
| `deps` | Lean 4.23.0, Mathlib, REPL and cslib at `[paths].lean_project`; the harness's two venvs; ripgrep (`AIProver/AIProver_plugin/setup.sh deps` with this config) |
| `claude` | links `~/.config/aiprover_orchestration/{root,aiprover.toml}`; installs the plugin from the local marketplace |
| `doctor` | `aiprover doctor --full`: environment, model endpoint, one live AIProver rollout, the lean-lsp tools |

All PASS means ready; `AIProver/AIProver_plugin/STARTUP.md` §5 maps each
FAIL to its fix. The skill runs `aiprover doctor` itself before every run.

**4. Use it.** In Claude Code or VS Code, ask for a problem to be run with
the aiprover-captain skill, by uuid of `data/*.jsonl` or with an informal
statement and proof. Results are written to `results/<run_id>/`.

Needs: Linux, Python 3.12 (venvs) and ≥ 3.11 (CLI), `jq`, `claude` logged
in, this repository with its `AIProver` submodule, ~10 GB disk (Mathlib).
The lean-lsp MCP tools come with the AIProver plugin
(`AIProver/AIProver_plugin/setup.sh claude`).

## Layout

| Path | Content |
|---|---|
| `aiprover.toml` | the one config: endpoint, runtime, paths |
| `setup.sh` | provisioning, plugin installation, verification |
| `claude_code/skills/aiprover-captain/SKILL.md` | the captain procedure |
| `…/references/` | Lean gates, subagent prompts, run record |
| `…/scripts/env.sh` | repository paths and the recording functions |
