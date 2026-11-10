# Configuration

## Run configs

A run config (`configs/orchestrator/*.json`) gives each role a backend and a
model, and the run's settings (fields of `orchestrator/config.py`).

| Backend | Serves |
| --- | --- |
| `claude` | Claude Code with its subscription, or the API when `ANTHROPIC_API_KEY` is set (`CLAUDE_AUTH=subscription\|api` overrides) |
| `codex` | Codex with its login |
| `openai_compatible` | Any server with that API, with Lean tools if `tools` is set |
| `aiprover` | The AIProver harness on a model server (prover only) |

Any backend serves any chat role, including the orchestrator and its
escalations. Costs are reported in tokens, and in USD where a price is known;
a USD limit counts tokens without a price at 100,000 per USD.

## Deployment settings

A deployment's own settings lie in `configs/site.json`, which is untracked;
`AIPROVER_SITE` names another file. Without it, the web server offers the
model server that `./setup` connects (GPU group `local`) and group `cpu`
(no model server), with `configs/orchestrator/default.json`.
`configs/site.example.json` lists every key:

| Key | Meaning |
| --- | --- |
| `served_config` | Run config of the web page, before the chosen models |
| `default_group` | GPU group the page selects first |
| `persistent` | The always-on model server: its group `name`, harness `config` and `proxy_port`, `label`, the `upstream`, `model`, and `sessions` that per-user servers share, per-run `timeout` and `session_slots`, and its `hardware` key |
| `cluster` | A Slurm cluster reached over an SSH ControlMaster: `host`, `control_socket`, the `submit` command, `partition`, `nodes`, `wall_time`, the `tunnel_config`, and the `checkpoints` a run may select |
| `groups` | GPU groups whose server jobs run on the cluster: `job_name`, `handoff`, `tunnel_config`, `prover_config`, `proxy_port`, `borrows`, `hardware` |
| `hardware` | GPUs and hourly price of each server from a date, for the reported GPU cost |
| `host_address`, `python` | The host and interpreter of per-user servers |

The cluster's `submit` command receives the checkpoint, partition, nodes, and
wall time, with `HANDOFF` and `SBATCH_JOB_NAME` exported; it prints
`Submitted batch job N`, and the job writes `node=<n> port=<p> job=<id>` to
the handoff file. The web server submits a group's job when a run of the
group needs the model, and cancels it after `QUERY_SERVER_IDLE_SECONDS`
without work.

## Limits and access

Concurrency per machine (hosted calls, Lean processes, AIProver sessions)
follows from its memory and processors; `AIPROVER_MAX_HOSTED`,
`AIPROVER_MAX_TOOL_CALLS`, `AIPROVER_MAX_LEAN`, `AIPROVER_MAX_SESSIONS`, and
`AIPROVER_LEAN_MB` override it. `QUERY_SERVER_MAX_RUNS` (default 2) bounds
the runs in progress per GPU group.

The web server has two pages: `/user`, where `/` leads, offers the persistent
group and the runs submitted from it; `/dev` offers every GPU group, model,
and dataset. The pages scope what is offered; they are not access control.
Access is open; `QUERY_SERVER_AUTH=on` requires tokens
(`configs/server/tokens.example.json`) and approval.
