# Models

A run uses two kinds of model. The AIProver model proves the lemmas and is
served on GPUs by vLLM, whereas the chat roles (orchestrator, checker,
judge, reviewer, and writer) may use any OpenAI-compatible service, Claude
Code, or Codex. The two need not share a machine: the GPU host runs only
vLLM, and the orchestration runs on any machine that reaches the host over
HTTP, so the GPU host needs neither this repository nor Nix.

## Serving the AIProver model

The weights are FP8, about 112 GB, so the GPUs need well over 112 GB in total
plus room for the KV cache. One node with two or more 80 GB GPUs runs the
model with tensor parallelism; two nodes with one large GPU each run it with
pipeline parallelism (`PP=2`) in a Ray cluster. The checkpoint, a folder with
`params.json` and `tekken.json`, is not part of this repository.

On the GPU host, vLLM is installed and the serve script of the AIProver
repository (the `AIProver/` submodule) is started with the checkpoint:

```bash
pip install vllm
PORT=8041 AIProver/AIProver_plugin/serve/serve_aiprover.sh /path/to/aiprover_model
curl http://<gpu-host>:8041/v1/models      # lists aiprover-model once loaded
```

The script passes the flags that the harness requires: the Mistral tokenizer
and format, tool calling, the reasoning parser, and the full context length.

| Setting | Effect |
| --- | --- |
| `PORT` | Port of the server (default 8041) |
| `SERVED_NAME` | Model name in requests (default `aiprover-model`) |
| `TP`, `PP` | Tensor- and pipeline-parallel sizes (default: all GPUs of the node, `PP=1`) |
| `MAX_MODEL_LEN` | Context length (default 1,048,576) |
| `EXTRA_ARGS` | Further arguments of `vllm serve` |
| `DRY_RUN=1` | Print the `vllm serve` command and run nothing |

With `PP=2`, the Ray cluster is started before the script: `ray start --head`
on the first node and `ray start --address=<first-node>:6379` on the second,
after which the script runs on the first node.

## Connecting the orchestration

On the machine of the orchestration, `./setup` connects the server through a
local reasoning proxy and checks the connection
([setup guide](setup.md)):

```bash
./setup --endpoint http://<gpu-host>:8041            # a host reachable from this machine
./setup --endpoint http://<gpu-host>:8041 --via HOST # a host reachable only through an SSH host
./setup --via HOST --handoff /path/on/HOST/server.txt # a cluster job that writes node=<n> port=<p>
```

The prover of `configs/orchestrator/standard.json` and of the web page's
default selection then uses this server. Without a GPU host,
`./setup --endpoint none` leaves every role, the prover included, to chat
models.

## Persistent server or cluster jobs

The AIProver model is deployed in one of two ways, and a deployment may use
both, each as its own GPU group in `configs/site.json`
([configuration guide](configuration.md)).

A persistent server runs continuously on a GPU host, started as above or as
a service of that host. Runs use it without waiting, and the per-user
servers of a shared host forward their sessions to it. Its GPUs, however,
are held while idle. The site's `persistent` entry names its group, its
address (`upstream`), its served model, and the sessions it serves to all
users; without a site file, the server that `./setup` connects is the
persistent group `local`.

A cluster deployment starts the model as a Slurm job only while runs need
it. The web server reaches the cluster's login host over an SSH
ControlMaster (`cluster.host`, `cluster.control_socket`) and calls the
site's `submit` command with the checkpoint, partition, number of nodes, and
wall time, exporting `HANDOFF` and `SBATCH_JOB_NAME`. The command submits a
job that starts vLLM as above, for example `serve_aiprover.sh` on two nodes
with `PP=2`, and the job writes `node=<n> port=<p> job=<id>` to the handoff
file once the server answers. The web server tunnels to that address, and
it cancels the job after 30 minutes without work
(`QUERY_SERVER_IDLE_SECONDS`). Consequently, GPUs are allocated only for
work, at the cost of the queue wait and the model's loading time at the
start of each job. While runs are in progress, the web server keeps a
second job queued behind the running one, so that a job reaching its wall
time is replaced without a full wait in the queue, and the sessions
interrupted by the change are repeated without counting as attempts.

A persistent server therefore suits a host with dedicated GPUs and frequent
runs, whereas cluster jobs suit shared allocations billed by use. On the
command line, `./setup --via HOST --handoff FILE` follows a cluster job's
handoff file in the same way, while the job itself is submitted by hand.

## Chat models of other providers

A provider with an OpenAI-compatible API serves any chat role through the
`openai_compatible` backend. A run config, for example a copy of
`standard.json`, names the provider's address, its model, and a file that
holds the key, which keeps the key out of the trace:

```json
"orchestrator": {
  "backend": "openai_compatible",
  "base_url": "https://<provider>/v1",
  "model": "<model id>",
  "api_key_file": "/path/to/key",
  "hosted": true,
  "tools": true,
  "max_output_tokens": 32000
}
```

With `tools`, the model checks Lean through the session tools; `hosted`
counts its tokens toward the run's hosted-model budget, and `extra_body`
passes parameters specific to the provider. The same entry serves the
checker, reviewer, and writer, and a `phases` entry gives a phase such as
`escalation` a model of its own. The command line runs the config directly:

```bash
aiprover/nix/tools/bin/python3 -m aiprover_orchestration.orchestrator.run \
  --config configs/orchestrator/<name>.json --statement statement.tex --proof proof.tex
```

The web page offers a config as its default when `served_config` in
`configs/site.json` names it ([configuration guide](configuration.md)). The
page's "Model servers" form adds servers that need no key, since the page
accepts no API keys while the server runs without authentication.
