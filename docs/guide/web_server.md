# Web server

```bash
aiprover/nix/tools/bin/python3 -m uvicorn aiprover_orchestration.server.app:app --port 8443
```

Open http://127.0.0.1:8443/dev, or install the service with
`scripts/systemd/install aiprover_query_server`;
`systemctl --user restart aiprover_query_server` loads a code change, and runs
in progress continue. The page's Guide explains it to readers new to Lean;
`node scripts/export_guide.js` writes the guide as a standalone page,
`docs/user_guide.html`.

## Runs

The Submit page takes an informal statement and proof, a Lean statement, or a
problem of a dataset in `data/`, and a model for each role. Its Task switch
chooses between formalizing the given proof and proving the theorem: a run in
the second mode withholds any given proof, keeps a Lean statement exactly as
given, and drafts its own proof before decomposing it. Its "Orchestrator
settings" set escalations, retries, attempts, session limits, the
hosted-model budget, and optional stages such as counterexample tests, open
subgoals, arXiv search, and review of the proof plan. A run follows a
library, takes a name that becomes its id, may wait for another run to end,
and chooses a Lean release; one not installed is downloaded with its matching
Mathlib, REPL, and CSLib.

Each run shows its progress, its blueprint (the lemmas as a dependency graph,
with the statement, proof idea, and Lean statement of each), a replay of every
stage, job, and session, its files, and its details (cost, GPU time, and
verification).

A cancelled or unproved run can be resumed from its trace, and the page asks
for a higher limit if the run reached its hosted-model budget. With "Require
review of the proof plan before proving", a run stops after its sketch and
presents the plan as a blueprint, which the reviewer approves, approves with
comments, returns for revision, or rejects. Generate documents writes the
report, the informal proof, and the blueprint of a proved run that lacks them.
Import run adds a run started elsewhere from a zip of its `results/<run_id>/`
folder or of its export, and the export downloads a run as one zip.

## Projects

The Projects tab holds the results of one subject as a chain on a shared
library. A project starts from a goal and optional source files.

Plan blueprint proposes the chain, which remains editable until it starts.
Research has one session answer a question and a second session referee the
answer; the report adds open lemmas, together with claims that lack a Lean
proof. A claim may be confirmed or rejected by a person, checked by a model,
or added as a result to prove in Lean, and only the last of these settles it.
Launch then proves the results in order, each by its own run on the shared
library, and Paper writes them up as one article.

## Libraries

A library is one Lean file or a Lean package uploaded as a zip, which the
server builds on the chosen Lean release and refuses on `sorry`, axioms,
`native_decide`, or code that runs. Every file of a run on a package imports
it, and agents read its statements, with proofs elided, through their tools;
`python -m aiprover_orchestration.search.library_index libraries/<name>`
adds it to the semantic search that provers query.

## arXiv search

With "Orchestrator searches arXiv abstracts and reads papers", or a project's
"Search arXiv" option, agents run `paper-search` over a local index of arXiv
math abstracts, ranked by embedding similarity fused with keyword scores, and
read chosen papers a page at a time, with formulas as LaTeX. Queries stay on
the machine; the paper service alone fetches papers from arXiv, and a
config's `paper_search_before` hides later papers from benchmark runs.

## Model servers and sign-in

The first run that selects a hosted service without a login prompts for its
sign-in on the page. "Model servers" adds any OpenAI-compatible server that
answers from the host, as a chat model or as an AIProver server; the page
accepts no API keys while the server runs without authentication.

## A shared host

Each user of a host runs a server of their own, under their own account,
since hosted roles run on the login of the account that runs the server.
With an account on the host, one command from the user's machine starts the
server and forwards its page:

```bash
ssh -t -L PORT:127.0.0.1:PORT <user>@<host> <checkout>/scripts/user/aiprover-server serve
```

`PORT` is fixed per account (20000 + 2 × (uid mod 5000)); `aiprover-server
ssh` on the host prints the complete command. Then open
`http://localhost:PORT/user`. Ctrl+C closes the connection while the server
and its runs continue; `aiprover-server stop` ends the server. A server keeps
its state in `users/<account>` of the checkout and runs at most 6 AIProver
sessions (`AIPROVER_MAX_PARALLEL`) and 2 runs (`QUERY_SERVER_MAX_RUNS`) at
once. The model server that all users share is the site's
`persistent.upstream` ([configuration](configuration.md)). The host's owner
prepares the folder once:

```bash
mkdir -p users && chmod 1733 users
setfacl -d -m u::rwx,u:$(id -un):rwx,g::---,o::---,m:rwx users
```
