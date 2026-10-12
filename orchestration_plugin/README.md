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
git clone --recurse-submodules <repository url> aiprover_orchestration && cd aiprover_orchestration && ./setup --endpoint dgx --via <host>
```

`./setup` (repository root) checks the platform and fetches the submodules.
It finds Nix, or offers to install it, asking first and showing the command.
It builds the environment from `flake.nix` and writes this directory's
`aiprover.toml` for the device. Finally it runs `setup.sh lean claude doctor`.
`./setup --dry-run` prints every step without changing anything.

| Endpoint | Model server | Connection |
|---|---|---|
| `--endpoint dgx [--via HOST]` | `aip-leanstral` on cic-dgx-01 (port 8040) | direct when reachable from the device (TACC network); otherwise an SSH tunnel through `HOST`, which must reach the DGX: a host name or an alias of `~/.ssh/config` (its `User` and key apply); key-based, no passphrase prompt (BatchMode) |
| `--endpoint vista [--user USER]` | a Vista server job | SSH tunnel to the compute node named in the job's handoff file, over an SSH master opened by hand (MFA) |
| `--endpoint URL [--api-key KEY]` | any OpenAI-compatible endpoint serving the model | direct; the key is sent as a bearer token |

The SSH tunnel is opened, checked and reopened by the AIProver CLI. Without
`--endpoint`, `./setup` keeps an existing `aiprover.toml`, or asks for one.

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
| `doctor` | `aiprover doctor`: environment, model endpoint, the model thinks |
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

To change the endpoint later, run `./setup --endpoint ... --no-lean`, or edit
`aiprover.toml` and run `setup.sh doctor`.

## Layout

| Path | Content |
|---|---|
| `aiprover.toml` | written by `./setup`: endpoint, runtime, paths (not in git) |
| `setup.sh` | the steps inside the environment: Lean, plugin, doctor |
| `claude_code/skills/aiprover-captain/SKILL.md` | the captain procedure |
| `…/references/` | Lean gates, subagent prompts, run record |
| `…/scripts/env.sh` | repository paths, tools on `PATH`, recording functions |
| `../flake.nix`, `../nix/` | the Nix environment: tools, virtualenvs, the lean-lsp-mcp patches |
