# AIProver orchestration

Tools for mathematicians to direct AI agents at their problems in Lean 4. An
orchestrator formalizes a problem, has the formalization audited blind,
decomposes the proof into lemmas and proves them with Claude and the AIProver
model; a theorem library records what is formalized and proved; a query
server exposes both over HTTP. All verification is local (Lean v4.23.0,
Mathlib v4.23.0, `autoImplicit false`).

```mermaid
flowchart LR
    M[Mathematician] --> S[Query server<br/>server/]
    S --> O[Orchestrator<br/>orchestrator/]
    S --> L[Theorem library<br/>library/]
    O -->|captain, auditor,<br/>reviewer, writer| C[Model APIs<br/>agents/]
    O -->|solvers| A[AIProver harness<br/>AIProver/]
    A --> V[AIProver model<br/>TACC Vista / DGX]
    O --> W[Lean workspace<br/>orchestration_workspace/]
    L --> W
```

## Layout

```
├── aiprover_orchestration/     # Python package
│   ├── paths.py                #   repository locations
│   ├── lean/                   #   Lean checks, module builds, source parsing
│   ├── agents/                 #   model backends (Claude, OpenAI-compatible,
│   │                           #   AIProver) and the routing pool
│   ├── library/                #   theorem library: store, verifier, CLI, HTTP API
│   ├── orchestrator/           #   formalize → audit → sketch → prove → assemble
│   │   ├── stages/             #     one module per stage
│   │   ├── prompts/            #     system prompts and templates (.md)
│   │   ├── reports/            #     LaTeX report and informalization
│   │   └── trace_view/         #     trace walkthrough and replay pages
│   ├── server/                 #   query server, job worker, Vista jobs, proxy
│   ├── roles/                  #   captain, auditor and solver role guides
│   └── utils/                  #   smoke test
├── configs/                    # orchestrator runs, AIProver harness, server tokens
├── orchestration_workspace/    # Lake project of library and run modules
├── scripts/                    # systemd units, Vista and local model serving
├── orchestration_plugin/       # Claude Code skill aiprover-captain (interactive captain)
├── setup, flake.nix, nix/      # one-command setup of the skill's environment (Nix)
├── AIProver/                   # AIProver harness and prover (submodule)
├── aiprover/                   # AIProver runtime: venvs, ripgrep, jobs; nix/ out-links
├── data/ libraries/            # problem datasets; single-file result libraries
├── results/ analysis/          # run outputs (traces, Lean, reports); analyses
└── state/                      # databases of the server and the library
```

Documentation in `docs/`: `orchestration.md` (system, design and analysis),
`AIProver_README.md` (orchestrator components), `aiprover_tacc.md`
(deployment), `server.md` (query server), `openai_math_problems.md`
(OpenAI math release problems in `data/openai_math.jsonl`); work log in
[logbook.md](logbook.md).

## Setup: the aiprover-captain skill in Claude Code

The skill `aiprover-captain` runs the orchestration from the Claude Code app,
VS Code or the CLI, with the session's agent as captain and AIProver jobs as
solvers (`orchestration_plugin/README.md`, `docs/orchestration.md` §1.5).
Lean and the AIProver harness run on the user's machine; only model inference
goes to a model server.

### Install

One command from a fresh clone, on Linux, WSL2 or macOS (about 15 GB of disk):

```bash
git clone --recurse-submodules <repository url> aiprover_orchestration
cd aiprover_orchestration
./setup --endpoint http://<model host>:<port>      # forms below
```

| Step of `./setup` | Result |
|---|---|
| Nix | found, or installed after confirmation (`--installer official\|determinate`) |
| `nix build` (`flake.nix`) | `aiprover/nix/tools`: Python 3.12, jq, ripgrep, git, curl, GNU userland, flock, pdflatex; `aiprover/nix/venv_vibe`, `aiprover/nix/venv_mcp`: the AIProver harness virtualenvs (uv2nix, pinned to AIProver's lock files, lean-lsp-mcp patches applied) |
| Endpoint | `orchestration_plugin/endpoint.toml` (connection, proxy) and `aiprover.toml` (harness) written for this device |
| Lean | elan and Lean v4.23.0 in `~/.elan`; Mathlib (prebuilt cache), REPL and cslib in `aiprover/lean_project` |
| Plugin | marketplace `aiprover-orchestration-local`, plugin `aiprover-orchestration`, user scope |
| Connection | the SSH master and tunnel of an SSH endpoint (asks for the host's credentials once); the local reasoning proxy |
| Doctor | environment, Lean, virtualenvs, model endpoint |

`./setup --dry-run` prints every step without changing anything; `--yes`
accepts the defaults; `--lean-project PATH` reuses an existing Lean project.
The system's own `ssh` is used, with the user's `~/.ssh/config`.

### Model endpoints

The AIProver model is served by an OpenAI-compatible server (vLLM, see
`AIProver/AIProver_plugin/serve/`). `./setup` writes the connection to it
into `orchestration_plugin/endpoint.toml`:

| Where the server is | Command | Connection | Authentication |
|---|---|---|---|
| Reachable from this device | `./setup --endpoint http://<host>:<port> [--api-key <key>]` | direct | none, or the key as bearer token |
| Reachable only from an SSH host | `./setup --endpoint http://<host>:<port> --via <ssh host> [--user <name>]` | SSH tunnel through the SSH host | the SSH host's credentials (password, one-time code, key), once per login session |
| A scheduled job on a cluster | `./setup --via <login node> --handoff <file>` | SSH tunnel to the node and port the job writes to `<file>` on the login node (`node=<n> port=<p>`); a restarted job is followed | the login node's credentials, once per login session |

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

`--endpoint` with a URL that answers directly is used directly; otherwise
`--via` is required. `--via` takes a host name or an alias of
`~/.ssh/config`, whose `User` and key then apply. `--timeout SEC` and
`--max-turns N` set the budget of one AIProver session (default 5400 s,
200 turns); a slower server needs a longer clock.

SSH routes authenticate once: `orchestration_plugin/setup.sh connect` opens
an SSH master connection and asks for what the host requires; the AIProver
CLI opens and reopens its tunnels over it without prompting. The master
closes on reboot, sleep or a network change; then run `connect` again. To
change the endpoint, run `./setup --endpoint ... --no-lean` (connection and
doctor only).

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

| `orchestration_plugin/setup.sh` | Effect |
|---|---|
| `connect` | open the SSH master (prompts for the host's credentials) |
| `endpoint` | `connect`, the SSH tunnel and the local reasoning proxy |
| `stop` | stop the local reasoning proxy (and the keep-awake on macOS) |
| `doctor` | check the environment, the endpoint and structured tool calls |
| `full` | `doctor` plus one live AIProver rollout |
| `claude` | reinstall the plugin (after editing the skill) |
| `lean` | rebuild the Lean project |

The doctor's row "harness is the pinned champion" fails while the AIProver
submodule carries the orchestration's changes; a run does not need it.

### Run

1. In a terminal, open the connection: `orchestration_plugin/setup.sh endpoint`
   (asks for the SSH host's credentials when needed). Of `data/`, only the
   sample set `data/openai_math.jsonl` is in git; copy other datasets
   (`data/*.jsonl`) from a machine that has them to run their problems by uuid.
2. Start the Claude Code app (restart it after an installation) and open the
   repository; `/plugin` lists `aiprover-orchestration`.
3. Ask for a run, by uuid of `data/*.jsonl` or with an informal statement and
   proof:
   ```
   /aiprover-orchestration:aiprover-captain Run OpenAIMath_011_TotientFibersFromSmoothShiftedPrimes from data/openai_math.jsonl
   ```
   or in words: "Use the aiprover-captain skill to formalize and prove
   <uuid>", or paste `<informal_theorem>…</informal_theorem>` and
   `<informal_proof>…</informal_proof>`.
4. Allow Bash for the project (or use auto mode): the captain runs many
   commands and waits up to 10 min per call for AIProver jobs. A run takes
   hours; keep the session open. If the SSH master closes, the captain asks
   for `orchestration_plugin/setup.sh connect` to be run in a terminal.

| Output in `results/<slug>_skill_<date>/` | Content |
|---|---|
| `trace.jsonl` | every Lean check, AIProver job, subagent call and decision, appended live |
| `<slug>_standalone.lean` | the verified Lean file |
| `Def_*`, `Thm_*`, `Sol_*`, `Check_*`, `module_build.log` | module check that `solution` has the target's type |
| `summary.json` | status (`proved`, `review_flagged`, `lemmas_unproved`, ...) |
| `informal/` | informalization (LaTeX, PDF) |

Trace pages at any point:
`python3 -m aiprover_orchestration.orchestrator.trace_view results/<run>/trace.jsonl`.
Jobs: `AIPROVER_CONFIG=orchestration_plugin/aiprover.toml AIProver/AIProver_plugin/bin/aiprover list`.
`nix develop` (or `direnv allow`) gives a shell with the same tools.

## Usage

```bash
# One orchestration run
python -m aiprover_orchestration.orchestrator.run \
    --config configs/orchestrator/claude.json \
    --problem-uuid JiatuBook_BoundedArithmetic_000004

# Resume a run from results/<run_id>/ alone (trace.json or trace.json.gz)
python -m aiprover_orchestration.orchestrator.run --run-id <run_id> --resume

# Semantic index of the pinned Mathlib, experimental (service: aiprover_mathlib_index)
python -m aiprover_orchestration.search.server --index data/mathlib_index

# Copy runs to a sharing repository (timer: aiprover_share_results)
python -m aiprover_orchestration.utils.share_results \
    --target ~/workspace/orchestration-results --match OpenAIMath

# Query server (systemd unit: scripts/systemd/aiprover_query_server.service)
uvicorn aiprover_orchestration.server.app:app --host 0.0.0.0 --port 8443

# Theorem library
python -m aiprover_orchestration.library add-theorem NAME FILE --statement "..."
python -m aiprover_orchestration.library verify NAME FILE [--disprove]
python -m aiprover_orchestration.library open-leaves NAME

# Trace pages, report, informalization of a finished run
python -m aiprover_orchestration.orchestrator.trace_view results/<run_id>/trace.json
python -m aiprover_orchestration.orchestrator.reports.informal results/<run_id>

# End-to-end test of the library (Claude Haiku)
python -m aiprover_orchestration.utils.smoke
```

Each role in a run config names a backend: `claude` (Anthropic API),
`openai_compatible` (a local server by `base_url`, or a hosted provider),
or `aiprover` (solver only), e.g.

```json
{"backend": "claude", "model": "claude-opus-5-5", "effort": "high"}
{"backend": "openai_compatible", "provider": "openrouter", "model": "qwen/qwen3-235b-a22b"}
{"backend": "openai_compatible", "provider": "huggingface", "model": "Qwen/Qwen3-235B-A22B"}
{"backend": "openai_compatible", "provider": "openai", "model": "gpt-5"}
```

Requirements of the pipeline and the query server (orchestration VM; the
skill's setup is above): elan with Lean v4.23.0; Mathlib v4.23.0 in
`~/workspace/lean_projects/TmpProjDir` (linked by `orchestration_workspace/`);
Python 3.12 with `fastapi`, `httpx`, `uvicorn`, `aiohttp` and `pygments`;
`pdflatex`; `anthropic`; a key for each hosted backend a config uses
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HF_TOKEN`, `OPENROUTER_API_KEY`);
AIProver venvs and ripgrep from
`AIPROVER_CONFIG=configs/aiprover/vista.toml AIProver/AIProver_plugin/setup.sh venvs rg`.

## Results

| Problem | Status | Lemmas | Final review |
|---|---|---|---|
| OpenAIMath 332, metric Markov cotype of L1 | proved (2026-10-10) | 49 | faithful |
| OpenAIMath 011, large totient fibers, conditional on (H) | proved (2026-10-11) | 20 | faithful |

The records of both runs (trace, Lean files, report, informalization) are in
`results/<run_id>/`. Further runs and their status are recorded in `logbook.md`.

## Future work

- Project upload: import an existing Lean project (declaration graph,
  definition and theorem stubs, original proofs as sketches), so
  mathematicians can build on their own formalizations.
- Orchestrator runs that read and extend the theorem library in place of
  the single-file libraries of `libraries/`.
- Missions and milestones; several pinned Lean environments.
