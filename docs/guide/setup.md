# Setup

`./setup` takes a fresh clone to a working installation on Linux, WSL2, or
macOS, with about 15 GB of disk. It checks the platform, fetches the
submodules, installs Nix if needed (after confirmation; sudo once, to create
`/nix`), builds the tools and the AIProver virtualenvs into `aiprover/nix/`,
builds the Lean project, connects the AIProver model server through a local
reasoning proxy, installs the Claude Code plugin, and ends with a check of
the installation.

## Commands

```bash
# An AIProver model server reachable from this machine,
# with the arXiv index for paper search.
./setup --endpoint http://gpu-host:8041 --search

# No model server: every role, the prover included, uses a hosted model.
./setup --endpoint none

# A model server reachable only from another host: an SSH tunnel through it.
./setup --endpoint http://gpu-node:8041 --via login-host

# A cluster job that writes `node=<n> port=<p>` to a file on the login host:
# the tunnel follows the job across restarts.
./setup --via login-host --handoff /path/on/login-host/server.txt

# Change the endpoint only, keeping the Lean build and the plugin.
./setup --endpoint http://other-host:8041 --no-lean

# Print the steps without changing anything.
./setup --endpoint none --dry-run
```

Run without `--endpoint`, `./setup` keeps the endpoint it wrote before, or asks
for one.

## Options

| Option | Effect |
| --- | --- |
| `--endpoint URL\|none` | The AIProver model server (OpenAI-compatible), or none |
| `--via HOST` | Reach the server through this SSH host (an alias of `~/.ssh/config` works) |
| `--user USER` | SSH user, when `~/.ssh/config` sets none for the host |
| `--handoff FILE` | With `--via`: a file on the host where a server job writes its node and port |
| `--api-key KEY` | Bearer token of the model server, for a direct connection |
| `--timeout SEC` | Wall clock of one AIProver session (default 5400) |
| `--max-turns N` | Turn budget of one AIProver session (default 200) |
| `--search` | Build the search virtualenv (CPU torch) and download the arXiv index (1.6 GB) |
| `--lean-project PATH` | Use an existing Lean project instead of `aiprover/lean_project` |
| `--harness PATH` | Use an AIProver harness checkout instead of the `AIProver/` submodule; kept until given again |
| `--no-lean` | Skip the Lean build and the plugin installation |
| `--no-plugin` | Link the configuration but install no Claude Code plugin |
| `--yes` | Accept every default |
| `--dry-run` | Print what would happen and change nothing |
| `--installer official\|determinate` | The Nix installer to use |

## Hosted models

Install [Claude Code](https://claude.com/claude-code), run `claude` once, and
log in with `/login`; Codex logs in with `codex login`. With no API key in the
environment, each hosted role runs through the logged-in program and its
subscription. A role that reaches a usage limit waits for the reset, and the
run continues. Check the login:

```bash
aiprover/nix/tools/bin/python3 -m aiprover_orchestration.agents.claude_cli --probe claude-opus-5-5
```

## AIProver model server

AIProver runs on vLLM behind an OpenAI-compatible API. The weights are FP8,
about 112 GB, so the GPUs need well over 112 GB in total plus room for the
cache: one node with two or more 80 GB GPUs (tensor parallel), or two nodes
in a Ray cluster (pipeline parallel, `PP=2`). The checkpoint is not part of
this repository.

```bash
AIProver/AIProver_plugin/serve/serve_aiprover.sh /path/to/aiprover_model   # PORT=8041 by default
```

## Search

`--search` downloads the prebuilt arXiv index
([LoganLuna/aiprover-arxiv-math-index](https://huggingface.co/datasets/LoganLuna/aiprover-arxiv-math-index))
into `data/arxiv_index`. To build or extend it instead:

```bash
python -m aiprover_orchestration.search.arxiv_index model data/arxiv_index    # the embedding model
python -m aiprover_orchestration.search.arxiv_index harvest data/arxiv_index  # arXiv metadata, resumable
python -m aiprover_orchestration.search.arxiv_index build data/arxiv_index    # embeds new or changed papers
```

The Mathlib index, which gives the prover semantic search over the pinned
Mathlib, is built with the steps in `aiprover_orchestration/search/__init__.py`.

## Services

`scripts/systemd/install <unit>...` writes systemd user units for this
checkout and account, with the interpreters that `./setup` builds, and
starts them. `sudo loginctl enable-linger $USER` keeps them running after
logout.

| Unit | Port | Role |
| --- | --- | --- |
| `aiprover_query_server` | 8443 | Web page, JSON API, and the queue of runs |
| `aiprover_paper_search` | 18571 | arXiv search and paper text for `paper-search` |
| `aiprover_mathlib_index` | 18570 | Semantic Mathlib search for the prover |

## Without Nix

The requirements are elan with Lean v4.23.0, the Mathlib project linked by
`orchestration_workspace/`, Python 3.12 with `fastapi`, `uvicorn`, `aiohttp`,
`pygments`, `anthropic`, and `psutil`, and `pdflatex`; the search services
add `torch`, `transformers`, `numpy`, and `huggingface_hub`.
