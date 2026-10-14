# Logbook

## Contributions

Theorem library (`aiprover_orchestration/library/`)
- Library of definitions and theorems over its own Lean workspace, with
  local verification under gating rules, sketches resolved through the
  decomposition graph, CLI and HTTP API, and role prompts.

Orchestration (`aiprover_orchestration/orchestrator/`)
- Automated formalize, audit, sketch, prove, assemble pipeline with captain,
  auditor and solver roles; role prompts of the theorem library.
- Pluggable agent backends: Claude, OpenAI-compatible endpoints (local
  vLLM, OpenAI, Hugging Face, OpenRouter), and AIProver jobs as solver.
- AIProver integration: sample extraction and Lean gate, fallback that keeps
  the answer's own theorem under its `open` commands, helpers spliced under
  the answer's `open` commands.
- Resumable runs reconstructed from the trace; infrastructure failures
  resumed without limit, ended after three resumptions without progress.
- Claude call budget per run; per-run settings over the served config.
- Attempts per lemma statement (`aiprover_attempts_per_lemma`); interrupted
  jobs do not count.
- Weighted session sharing across AIProver jobs (`aiprover_session_slots`):
  each job takes a share of the free sessions weighted by its lemma's failed
  attempts, so freed sessions go to the remaining, harder lemmas.
- Hand-back to the captain after repeated failures
  (`aiprover_handback_after`): the captain proves the lemma, splits it into
  new lemmas, restates it, or asks for a retry; every proposal is checked by
  Lean before it is applied, while the other lemmas keep running.
- Libraries of verified results: runs build on earlier verified results
  (definitions and theorems) and extend them, from the command line or the
  query server.
- Final review by an independent referee model (status `proved` only with
  verdict FAITHFUL) and a LaTeX report with PDF per verified run: the
  mathematics written by a writer model, the Lean code inserted verbatim,
  each statement checked against its code by the referee.
- Assembly in dependency order of the proofs, so proofs kept across a
  replan precede none of the lemmas they use.
- Replan prompt with the solvers' failed code and the Lean errors located in
  it.
- Interactive captain: skill `aiprover-captain` (`orchestration_plugin/`)
  runs the method in Claude Code with the session's agent as captain and
  subagents as auditor, reviewer and writer; runs are recorded in the same
  result layout and rendered by the same trace pages.
- Knowledge carried across attempts on a lemma (searches, files read, last
  reasoning, code), given to later sessions as guidance and curated by the
  captain at hand-backs (`orchestrator/knowledge.py`).
- Captain on Opus 5.5 with high effort and a 128,000-token reply limit.

Model serving and infrastructure (`scripts/`, `aiprover_orchestration/server/`,
`configs/aiprover/`)
- AIProver model served on TACC Vista (2 x GH200, vLLM, FP8 Mistral format,
  pipeline parallel), reached through an SSH ControlMaster tunnel.
- Query server: web page and JSON API, approval by token holders, on-demand
  Vista server jobs released after 30 min idle, concurrent runs, live
  progress.
- Reasoning proxy that records the model's reasoning per session and caps
  replies at 16,384 tokens, below the harness client's read timeout, so a
  reply that does not converge cannot hold a session until its clock; it
  repairs malformed past tool calls and their results and gives content to
  empty past replies, which vLLM would reject.

Trace pages (`aiprover_orchestration/orchestrator/trace_view/`)
- Walkthrough, timeline and replay of every run, updated while it runs,
  embedding prompts, replies, Lean returns, AIProver session transcripts
  and the model's reasoning.
- Replay as one tree per sketch, attempts stacked under each lemma,
  hand-backs and split lemmas in place, interrupted jobs left out.

Results
- JiatuBook BoundedArithmetic 000004 proved with the AIProver models.

## Todo

- Nix setup: first `./setup` on a Linux device with Nix (commit the
  generated `flake.lock`); then macOS, where the AIProver harness has not
  run before (`setup.sh full` as acceptance test).
- `aiprover-captain` skill: first end-to-end run in Claude Code on
  `JiatuBook_BoundedArithmetic_000004` with a model server up; compare
  with the pipeline's run of the same problem.
- `utils/share_results.py` selects runs by `trace.json` and `srv_*` run
  ids; extend it to skill runs (`trace.jsonl`) if they are to be shared.
- Decode benchmark (Vista jobs 1060653 tune, 1060649 arms A-D, 1060650
  arms E-G): tabulate with `scripts/vista/summarize_bench.py`; deploy
  `serve_aiprover_vista.sbatch` and `submit_aiprover_vista.sh` (CUDA graphs
  by default, the winning arm's settings) to `$WORK/aiprover_serve/scripts`
  once the arms pass `doctor` and greedy agreement.
- Stampede3: launch check of `serve_aiprover_s3.sbatch` (TP=4, 2 h);
  same loads as the Vista benchmark. Allocation `TG-CIS260432` holds 766
  SUs (expires 2027-03-16); the H100 charge rate per node-hour bounds the
  hours of server time this buys.
- Generalize `server/vista.py` (control socket, host, submission script) to
  a per-cluster setting so the query server can submit to Stampede3.

- Runs on the OpenAI math bank (`data/openai_math.jsonl`), starting with
  `OpenAIMath_332_MetricMarkovCotypeL1`.
- `OpenAIMath_004_HilbertTenthOverQ`: statement first, then the Section 2
  reduction; hypotheses (I3) and (I4) are elementary and can be run as
  separate problems.
- Orchestrator runs read and extend the theorem library in place of the
  single-file libraries of `libraries/`.
- On SIGTERM the orchestrator's main thread exits while its solver threads
  keep polling their AIProver jobs, so a terminated run lives until those
  jobs end; the threads should cancel their jobs when the run is
  interrupted.
- Library search, remaining items of `docs/proposed/mathlib_search.md`:
  retrieval before each job, captain adjudication of stated gaps (closure
  probe), coverage triage at sketch time; evaluate items 1, 2, 5, 6 against
  the baseline table there.
- Project upload with our own Lean meta-programs.
- Authentication for the HTTP API.
- Missions and milestones; several pinned Lean environments.

- On SIGTERM, cancel the run's AIProver jobs (`aiprover cancel`) so it
  unwinds at once; it now waits for in-flight jobs (up to 5400 s).

- Render `trace.html` automatically at the end of each run.
- Evaluate a Lean-specialized open-weights solver that fits 16 GB (for
  example Goedel-Prover-V2-8B or Kimina-Prover-Distill-8B in FP8) against
  Qwen3-4B-Instruct on `000004`.
- Resume `jiatu_000020_sonnet_haiku` (or fork `local_000020` at `prove`
  with another solver) to complete the 000020 attempt.
- Vista: set the AIProver checkpoint path; run `check_rl_compat.py` on it;
  submit `scripts/submit_aiprover_vista.sh <ckpt> gh-dev 2` as a launch check,
  then on `gh`; confirm `$SCRATCH` resolves to `/scratch/11757/loganluna`
  (handoff path in `aiprover_vista.toml`).
- VM: open `~/.ssh/vista.sock`; `doctor --full` with
  `aiprover/aiprover_vista.toml` (live rollout); run `vista_000004`.
- Compare `--cpu-offload-gb` and `QUANTIZATION=fp8` at 2 nodes against bf16
  at 4 nodes (throughput, solve rate).
- Replan prompt: require the captain to split a failed lemma rather than
  restate it.
- Query server deployment: tokens, systemd user unit, linger, port 8443 in
  the VM security group; a certificate for HTTPS.
- Resume re-proves every unproved lemma, including lemmas that already failed
  in the current sketch; `cobhamF_length_bound` was proved on such a retry.
  Make retries an explicit per-lemma attempt budget instead.
- Stop the remaining AIProver samples of a lemma once one sample verifies.
- Upstream the AIProver branch `resume-interrupted-sessions` (`aiprover
  resume`, automatic resumption after infrastructure faults, target statement
  check) to `PrithwishJana/AIProver` with the repository owner, then point the
  submodule at the merged commit.

## Issues

- The DGX server (vLLM 0.31.0) runs without a tool-call parser; every
  client must reach it through a reasoning proxy (the skill starts one
  locally). Restarting it with `--enable-auto-tool-choice --tool-call-parser
  mistral` would remove the dependency for other clients.
- `aiprover doctor` fails 4 of 15 rows under every config: harness pin and
  skill-copy comparison (uncommitted orchestration changes to `harness.py`
  and `aiprover.py` in the `AIProver` submodule), REPL and cslib not built in
  `TmpProjDir`. The skill's preflight treats them as non-blocking.
- The shared Mathlib build (`lean_projects/TmpProjDir/.lake/packages`) is
  writable by prover sessions. A session of 332 (job
  `20261010-005428-5xxt`, s0) ran `lake exe cache get` at 01:03 on
  2026-10-10; the cache's traces do not match the local build, so 5,239
  of 6,864 Mathlib modules became stale and each session's
  `lake setup-file` rebuilt them, about 15 builds in parallel (380 Lean
  processes for 74 modules, free memory down to 3.4 GB, no swap). Lean
  checks stalled until 07:40, when `lake build --no-build Mathlib`
  reported all targets up to date. Remedy: the package tree read-only,
  and the harness refuses `lake build|exe|update|clean` (`lake_refusal`).
  Cost: 11 failed attempts of 332 and 28 of 048 ran mostly inside the
  window, and 8 hand-backs of 048 were rejected because the Lean gate
  timed out (300 s), leaving those lemmas handed back without review.

- Vista server jobs named `aiprover_srv` are cancelled by our account from
  outside this server (1057849 at 00:58, 1060100 at 01:38 while pending,
  1060124 at 02:14 while pending; none logged by the worker, no scancel in
  the harness, the Vista scripts or the local sessions). Jobs of group
  `main` are now named `aiprover_srv_main`. Source identified on
  2026-10-10: `temp/vista_watch.py`, a watcher left running since
  2026-10-08, which submitted and cancelled `aiprover_srv` jobs outside
  the worker and wrote main's handoff file
  (`$SCRATCH/servers/aiprover_server.txt`). At 12:28 its job 1063487
  displaced main's server 1063220 from the handoff, and the worker closed
  main's tunnel. The watcher was stopped at 12:58.

- AIProver sessions receive the shared Lean project's path
  (`AGENT_MATHLIB`, `lean_projects/TmpProjDir`) and can build in it; its
  packages are linked into every session and into `orchestration_workspace`. A
  rebuild of the dependencies there invalidates the Mathlib traces, after
  which `lake build` recompiles Mathlib (about 25 min on the VM). The
  project's top level and `lakefile.lean` are read-only; its `.lake` stays
  writable for Lake.

- The AIProver orchestrator (OpenAI-compatible, 24,576 reply tokens) and the
  per-checkpoint server switching are untested on Vista.
- The served budget of 10 Claude calls is below the 12 used by
  `vista_smoke_000004`; runs with sketch repairs or replans stop before
  assembly.

- Solver calls in flight when a lemma is proved still complete and are
  billed; the timeline marks them as discarded (9 of 19 calls in
  `smoke_000004`).
- `local_000004`: the captain restated the same failed lemma in both replans
  instead of decomposing the induction into base and step lemmas.
- Local solvers reference constructors without their namespace
  (`ax_ITR_eps` for `Deriv.ax_ITR_eps`) and invent recursors
  (`Term.induction`); 16 of 45 checks failed on unknown identifiers.
- On SIGTERM the run waits for in-flight solver calls before unwinding;
  solver chains should observe a cancellation flag and end their calls.
- `local_000020` was started before outcome recording on termination, so its
  trace has no outcome; it remains resumable.
- `AIProver_plugin/setup.sh` (PartitionAndProve): the embedded Python of the
  scratch `warm_text` patch has literal newlines in place of `\n` escapes and
  does not parse; the patch was applied to `aiprover/venv_mcp` directly.
- `~/workspace/autoformalization-jtemb` links to `/home/pjana`, which is not
  readable here; the Lean project is built at `~/workspace/lean_projects`.
- `/work/11428/pjana/leanstral_hf_unpacked` does not exist and the
  `/scratch/11428/pjana` copies are not readable from this account; the
  per-expert copy is produced locally.
- AIProver sessions are long (up to `timeout` per sample) and the local server
  holds about one full-length session; 8 lemmas at 2 samples each take over an
  hour.

## Decisions

- Device setup uses Nix (flake, uv2nix virtualenvs) linked into
  `aiprover/nix/`, so the skill runs from the Claude Code app without a Nix
  shell; elan stays outside Nix (`~/.elan`), where the harness expects it.
  The leanfinder edit of the VM's lean-lsp-mcp is not carried over: it is
  part of the experimental Mathlib index, not of AIProver's setup.
- The `aiprover-captain` skill makes no programmatic model calls (no
  `claude -p`, no orchestrator driver, no `aiprover expand`,
  `backtranslate` or `ask`); blind roles are subagents of the session.
  Solvers are AIProver jobs only; hand-backs are the captain's.
- Skill runs record their trace as `trace.jsonl`, appended line by line
  from the shell; `Trace.load` reads it, so no trace writer process is
  needed.
- The budget keys keep the names `max_claude_calls`, `final_claude_calls`
  and `informal_claude_calls`, stored in existing traces, although they
  now count calls to every hosted model.
- Explicit Claude prompt caching (a `cache_control` block on the stable
  prefix of a hand-back) is deferred: hand-backs of a run are often more
  than the cache lifetime apart.
- Decode settings are measured on one allocation per arm set
  (`bench_serve_vista.sbatch`) before the serve defaults change; the
  serve script deployed on Vista keeps eager mode until then. FP8 KV cache
  stays opt-in (numerics differ); CUDA graphs, tuned MoE kernels and
  prompt-lookup speculation preserve the output distribution.
- MoE tuning uses the base checkpoint's `text_config` as a DeepseekV3
  config (same expert shapes); the tuner does not read the Mistral3 wrapper.

- OpenAI math release targets: families 332 (full proof), 011 (Theorem
  1.1 conditional on Theorem 1.2), 164 (Corollary 1.2 from Theorem 1.1),
  048 (statement only) and 259 (full proof, stretch), ranked by
  significance and Mathlib feasibility among the 130 families without a
  formalization (`docs/openai_math_problems.md`).
- Second selection by significance in public and expert reception: 004
  (Hilbert's tenth problem over Q; statement and Section 2 reduction
  conditional on the inputs of Sections 3-6) and 109 (integer
  multiplication below n log n; statement and audit). Hodge for CM abelian
  varieties (032) and BSD (002, 006) are excluded: their statements need
  definitions absent from Mathlib.
- Claude roles use the Anthropic API with a per-deployment key; hosted
  open-weights and OpenAI models use the OpenAI-compatible backend.
- Proxy reply cap below the harness client's read timeout (16,384 tokens
  at 29 tokens/s against 720 s).
- AIProver `verified` requires the target statement verbatim; other fixed
  declarations may differ, since the orchestrator splices the extracted
  proof under its own statements.
- One SQLite database per workspace (`state/library.db`); solution modules
  are named by target and submission id (`Sol_<theorem>_<id>`), so
  submissions of different theorems never share a module.
- Theorem statements import only Mathlib and definitions; a disproof
  imports no theorems.
- A sketch may depend on `sorryAx` only through imported library theorems;
  its own text is free of forbidden constructs.
- Resolution recomputes the least fixed point of the decomposition graph
  after every submission.

- A run uses one AIProver version for both roles; the worker runs together only
  the runs that share the served checkpoint, and a waiting run of another
  version holds back later runs that need a model server.
- Walkthrough is a single self-contained file (trace embedded as JSON); only
  KaTeX (MathML output, no stylesheet) and Google Fonts load from the network,
  with plain-text fallbacks.
- Absolute Lean attempt paths are reduced to file names in the page.
- Node heights in the replay follow their text lines; edge and phase labels
  carry a background halo so they stay legible over edges.
- Page templates in `orchestrator/` hold no run data; opened directly they
  display a notice pointing to `results/<run_id>/`.
- Replay positions are logical (stage, lemma, solver, round); time drives
  only when nodes appear and finish, so parallel chains run side by side.
- Both pages share `trace_common.css`/`trace_common.js`, inlined by the
  generator so each output file stays standalone.

- Subagent backends use the OpenAI chat schema so that a local server, a
  hosted open-weights model and a proprietary model are interchangeable;
  the orchestrator package stays standard-library only (HTTP via urllib).
- Local solver: Qwen3-4B-Instruct-2507 (Apache-2.0, already cached, 8 GB in
  bf16, non-reasoning instruct model with reliable tag-format following).
  8B Lean provers need about 16 GB in bf16 and exceed the card without
  quantization.
- vLLM runs with its native sampler (`VLLM_USE_FLASHINFER_SAMPLER=0`), which
  needs no JIT kernel build at startup.
- Tactic blocks returned with a leading `by` are normalized before splicing
  after `:= by`.

- Resume state is derived from the trace rather than a separate checkpoint
  file, so the trace remains the single record of a run.
- Captain calls on `claude` time out after 2400 s (`config.json`); a
  sketch for `000020` exceeded the 900 s default.

- AIProver runs with its own configuration file through `$AIPROVER_CONFIG`;
  the plugin is used in place and not copied.
- Model on Vista, everything else on the VM: compute nodes have no egress
  (captain calls) and the harness runs on the calling machine.
- Two-node TP: the bf16 checkpoint (~222 GiB) exceeds 2 x 95 GiB HBM; the
  surplus weights per rank are offloaded to Grace memory by default, which
  preserves the measured numerics; fp8 is an option.
- One Mathlib build (`~/workspace/lean_projects/TmpProjDir`) serves the
  harness and the orchestrator's workspace.
- Routing is kept as declared by the fused checkpoint's config
  (`topk_method: noaux_tc`, `scoring_func: softmax`, one group, top-4,
  normalized): transformers' `Mistral4TopkRouter` applies a softmax.
- Trained (fused) checkpoints are served after the same per-expert
  conversion.

- Query server runs one job at a time: runs share the modules of
  `orchestration_workspace/` and the Vista server (about 14 tokens/s per request).
- Served runs are subprocesses of `orchestrator.run`, so tracing, resumption
  and outputs are those of a manual run.
- Authentication by per-user bearer tokens; the web page holds the token in an
  HttpOnly cookie, which also authorizes the event stream and result links.

## Done

- Query server access is open by default: users run the server
  themselves, so no token is needed and submissions are queued without
  approval. Tokens, per-user visibility and approval remain available with
  `QUERY_SERVER_AUTH=on` (`server/app.py`, `docs/server.md`).

- GPU group `cpu` on the query server (`server/vista.py`): a group
  without a model server, for runs whose roles all use Claude or hosted
  models (for example Claude as captain and subagent). Submissions to it
  that select AIProver for any role are rejected; its runs have their own
  run slots and never wait for a Vista or DGX server.

- AIProver job inputs are isolated per submission
  (`agents/aiprover.py`): the context, statement and hint files of a job
  are written to a temporary directory of its own under
  `temp/<run_id>/aiprover/`, removed once the CLI has copied them. Job
  names are truncated to 60 characters, so lemmas of a run with a long id
  share one name and cannot key these files. A job is resumed only if its
  stored `context.lean` and `statement.lean` equal the lemma's current
  ones; otherwise a new job is submitted.

- Literature review `docs/lit_review/` (`lit_review.md`, `lit_review.xlsx`):
  30 related systems, feature matrix, gaps and ranked adoptions; MAESTRO,
  Paper2Lean and ToMAP (`docs/papers/`) read in full.
- Query server page organized around the run list: the new-run form opens
  from a "New run" button, with library and model/GPU settings collapsed
  behind summaries of the current choice; Vista server jobs are collapsed
  under the health bar; run detail appears only for a selected run.
- Query server page: Vista server jobs as a table per GPU group (state,
  actual or Slurm-estimated start, elapsed and limit, nodes, pending
  reason, worker status), from `squeue -o %S`.
- OpenAI math problem bank `data/openai_math.jsonl` (seven problems) and
  reference `docs/openai_math_problems.md`.
- `docs/orchestration.md` describes the whole system: deployment, GPU
  groups and model serving, run lifecycle, AIProver sessions and the
  reasoning proxy, traces, the theorem library, and the analysis.
- Query server, reasoning proxies, runs, results, logs, AIProver jobs and
  server state moved to this repository; the archived repository runs
  nothing.
- Reply cap below the harness client's read timeout and placeholder content
  for empty past replies in the proxy; target-statement check in AIProver
  grading; one orchestrator process per run on the query server.
- Repository restructured as one package, with regression checks against
  the previous code.
- Theorem library with CLI and HTTP API; Haiku smoke test passed.

- `orchestration.md`: design of the captain/solver loop (sketch, hand-back,
  replan, subagent information flow, assembly), failure
  analysis, contributions, ranked future work.
- Resumable AIProver sessions: `aiprover resume <job>` continues cut-off
  sessions (harness `AGENT_RESUME_SESSION`); resumed runs resume their
  unfinished jobs. Per-run `aiprover_timeout` (0 = turn limit only).
- Informalization stage (`orchestrator/informal.py`): the writer states and
  proves the theorem and every lemma in natural language (`<slug>_informal.pdf`),
  checked by the reviewer against the Lean source; also for runs with unproved
  lemmas.
- Smoke run `smoke_000004` proved and verified.
- Trace walkthrough, graph and replay pages for `smoke_000004`.
- Pluggable agent layer; local vLLM serving; run `local_000004`.
- Resumable runs with automatic restart after infrastructure failures.
- AIProver harness provisioned and integrated as the `aiprover` solver backend.
- Orchestration VM provisioned for the AIProver model on Vista; server job and
  configurations written.
- `vista_smoke_000004` proved and verified with the AIProver base model on
  Vista (Sonnet 5.5 captain).
- Query server with run queue, live progress and Claude call budget.
- Per-run model choice for orchestrator and subagent (Claude model and
  reasoning level, or AIProver trained/base) on the query server page.

## 2026-10-14: OpenAI math problems 004 and 109

- openai/math unchanged in content since `fd4aeeb` (one workflow commit,
  `1581d4c`); 004 and 109 remain without formalization.
- `OpenAIMath_004_HilbertTenthOverQ`: hypotheses (I0)
  Davis-Putnam-Robinson-Matiyasevich, (I1) rank one of
  $E: y^2=x^3-d^2x$ over $\mathbb Q(\sqrt2)$, $d=75-53\sqrt2$, (I2)
  formal-logarithm estimates, (I3) five-point height estimate, (I4)
  contact representations, (I5) pole parity, (I6) canonical height; the
  proof is Section 2 of the source (48 kB of TeX).
- `OpenAIMath_109_IntegerMultiplicationBelowNLogN`: statement for one
  multitape Turing machine (`Turing.FinTM2`, `TM2ComputableInTime` in the
  pinned Mathlib) with time $O(n(\lg n)^{1-\kappa})$, $\kappa=2^{-182}$;
  the proof is the source outline.

## 2026-10-14: Sketches for problems without a known proof

- Issue: on P versus NP the captain submitted one lemma equivalent to the
  theorem, with a main proof that only unpacked it, and stated that no
  progress was made.
- Decision: the sketch prompt requires an attempt when no proof is known:
  a named approach, lemmas each strictly simpler than the theorem, and
  conjectural lemmas allowed (tested by solvers, hand-backs and
  refutation). The replan prompt asks for a changed approach rather than an
  isolated equivalent lemma.
- The P versus NP trace was rewound to before the sketch (backup in
  `temp/trace_backup/`) and the run requeued; formalization, audit and
  setting check are kept.
- Its second sketch (P ⊆ P/poly and NP ⊄ P/poly) moved the difficulty into
  a lemma stronger than the theorem. Added a sketch judge: a second model
  (Sonnet, captain phase `judge`) checks each compiled sketch and returns
  REVISE for a lemma equivalent to or stronger than the theorem, an open or
  false lemma, a lemma that is not self-contained, or bundled steps; REVISE
  is a repair round, and the last round accepts a compiled sketch
  (`Config.sketch_judge`, default on). The run was rewound again and
  requeued.

## 2026-10-14: Setting facts and the preamble

- Issue: setting facts are compiled after the formalization's preamble
  (its `open` commands) but were appended to the definitions, which
  precede it. For `OpenAIMath_004_HilbertTenthOverQ`, whose definitions
  are in `namespace H10Q` and whose preamble is `open H10Q`, every sketch
  failed on the same unknown identifier inside the facts, and the run
  ended after the sketch repair budget.
- Fix: `Formalization.add_setting_facts` appends the preamble and the facts
  together (pipeline and resume). The run's last sketch compiles with it.
  004 is requeued to resume from its trace on group `openai`.

## 2026-10-13: Session policy

- Decisions, from the session accounting of the first 332 run: hand back
  after 2 failed jobs (`aiprover_handback_after`, was 3); Vista session
  limit 3,600 s (was 5,400; no verified session of that run exceeded 75
  min); at most 3 sessions per job (`aiprover_job_sessions`, new; jobs of
  5 or more sessions proved 1 of 15 lemmas). The DGX keeps 10,800 s.
- A session's answer is gated when the session ends
  (`AIProverAgent.solve(accept=...)`); the first accepted answer cancels
  the job's remaining sessions.
- A raised `maxHeartbeats` in an answer is kept on its helpers and on the
  lemma, capped at 400,000 (`lean.text.heartbeat_option`).
- The queued runs (332, P versus NP) start with these settings.

## 2026-10-13: Second run of problem 332

- `srv_20261013_195822_OpenAIMath_332_MetricMarkovCotypeL1` queued from
  scratch on group `main` with the settings of the first run (5 attempts
  per lemma, 2 replans, 60 Claude calls, 30 hand-backs, captain phases)
  and the session policy above, to measure the run on the current code (setting check, refute action,
  uniform hand-back schedule, semantic Mathlib index, per-run accounting).
  The first run's records stay in
  `results/srv_20261009_010221_OpenAIMath_332_MetricMarkovCotypeL1/`.
- The run uses the servers of groups `main` and `openai` (two requests of
  2 nodes). The `openai` request is held outside the worker's idle release
  and is cancelled by hand when the run ends.

## 2026-10-13: Upper-bound test on an open problem

- `data/open_problems.jsonl`: P versus NP, stated in natural language
  ($\mathsf{P}\ne\mathsf{NP}$ over Turing machines; the formalization
  defines both classes, which Mathlib lacks). No proof is supplied.
- Queued as `srv_20261013_184750_Open_P_vs_NP` on group `open` (Vista)
  with the budgets of the other runs (5 attempts per lemma, 2 replans, 60
  Claude calls, 30 hand-backs, session limit 3,600 s), setting check and
  refute action enabled.

## 2026-10-13: Refutation and setting check

- Refute action (`docs/ideas.md` 14; `docs/orchestration.md` 2.3, 3.2). A
  statement counts as false only when Lean accepts a proof of its negation,
  `theorem <name>_refuted : ¬ (type_of% @<name>)`, against the definitions
  alone and with the standard axioms (`SketchStage._refutation`). At a
  hand-back (`<refute>`) the lemma is marked refuted, no further job or
  hand-back starts, and the prove phase ends in a replan that lists the
  refutation, may not state the lemma again and is not counted against
  `max_replans`. In place of a sketch (`<refutation>`) the theorem is
  refuted and the run ends with status `disproved`
  (`<slug>_refutation.lean`).
- Setting check (`setting_check`, default on): before the first sketch the
  captain proves facts about the setting (contradictory hypotheses,
  degenerate structures, small cases) as theorems `setting_...`; the
  compiled facts are appended to the definitions (`setting_checked`).
- Decision: facts join the definitions rather than the sketch, so that
  solvers, hand-backs, replans and the final modules see them without a
  second mechanism; the target statement is unchanged.
- Decision: a refutation is checked without the other lemmas of the
  sketch, since unproved lemmas may themselves be false.
- Checked against Lean with recorded replies
  (`temp/refute_test/test_refute.py`, 14 cases: accepted and rejected
  refutations of lemmas and of the theorem, setting facts, resume).

## 2026-10-13: Run records of the proved OpenAI math problems

- `results/` holds the records of `OpenAIMath_011` and `OpenAIMath_332` as copied by
  `utils.share_results`: gzipped trace, config, summary, trace page, Lean modules,
  report and informalization. `*.gz` is stored in Git LFS.

## 2026-10-11: Sample problem set in git

- `data/openai_math.jsonl` (five problems: 332, 011, 164, 048, 259) is
  tracked as the sample set for the aiprover-captain skill; the rest of
  `data/` stays out of git. Source: openai/math, Apache-2.0.

## 2026-10-11: One-command setup with Nix

- `./setup` (POSIX sh, after the flywheel superproject's): platform and disk
  check, submodules, Nix (installed on request), `nix build` of `.#tools`,
  `.#venv-vibe`, `.#venv-mcp` into `aiprover/nix/`, `aiprover.toml` from an
  endpoint preset (`dgx`: direct when reachable, else SSH via `--via`;
  `vista`; URL), then `orchestration_plugin/setup.sh lean claude doctor`.
- `flake.nix`, `nix/tools.nix`, `nix/venvs.nix` (uv2nix, wheels only, Python
  3.12); `nix/python/{vibe,mcp}`: pins of AIProver's `requirements-*.lock`,
  `uv lock` for Linux and macOS (same versions; extra packages only under
  Windows or Emscripten markers); four lean-lsp-mcp patches taken from the
  VM's venv (`*.py.orig`), byte-identical to its patched files after
  application.
- `orchestration_plugin/aiprover.toml` is generated per device (absolute
  paths) and no longer tracked; `setup.sh` steps are `lean`, `claude`,
  `doctor`, `full`.
- SSH routes authenticate as TACC logins do: `setup.sh connect` opens an SSH
  master (`control_socket`, also for `dgx --via`) and prompts for the password
  and `TACC_Token`; the CLI's tunnels ride it. The system ssh client is used
  (not bundled): Nix's OpenSSH rejected Ubuntu-permitted `~/.ssh/config`
  permissions and lacks GSSAPI and macOS `UseKeychain`. `--via` accepts
  `~/.ssh/config` aliases; `ssh_user` is empty unless `--user` is given.
- Endpoints in `./setup` are generic: `--endpoint URL` (direct when it
  answers), `--via HOST` (SSH tunnel, control socket
  `~/.ssh/aiprover-<host>.sock`), `--handoff FILE` (server job on a cluster),
  `--timeout`, `--max-turns`; `dgx` and `vista` remain as shortcuts of the
  project's servers and are not documented in the README.
- Key offer in `setup.sh connect`: after a password login it offers a
  dedicated key (`~/.ssh/aiprover_ed25519`), installs it through the master
  with `restrict,port-forwarding,permitopen="<server>"` (or `command="cat
  <handoff>"`), and verifies a no-prompt login; on a host that answers
  "publickey with partial success" the key is removed again and the host
  remembered. Checked on the orchestration VM (`AuthenticationMethods
  publickey,keyboard-interactive keyboard-interactive`): partial success
  detected, key removed, `~/.ssh` restored.
- Connection resilience (macOS device: the SSH master dropped within minutes
  during runs, leaving sessions on the VM that never closed, i.e. network-level
  loss): master opened with `ServerAliveCountMax=20` (10 min tolerance instead
  of 90 s); `connect` first reopens without a prompt (`BatchMode`, works with
  keys, agents, Kerberos); on macOS the proxy is accompanied by `caffeinate
  -ims -w <proxy pid>`; `setup.sh stop`; `endpoint` closes a stale `ssh`
  forward holding the tunnel port and checks the path end to end.
- Endpoint-independent harness path: the harness always talks to a local
  reasoning proxy (`127.0.0.1:18575`), which forwards to the server directly
  or through the SSH tunnel. `./setup` writes `endpoint.toml` (connection,
  `[proxy]`) and `aiprover.toml` (harness, direct to the proxy); `setup.sh
  endpoint` opens master and tunnel (AIProver CLI with `endpoint.toml`) and
  starts the proxy detached, restarting it when its upstream changes. Checked
  on the VM against the DGX directly (no tool-call parser): model endpoint,
  model thinks and structured tool calls PASS through the local proxy.
- macOS device: environment built, doctor 14/15, tunnel answers; every
  AIProver job ended `empty`. Cause: the DGX server has no tool-call
  parser, and the device reached it directly instead of through the
  reasoning proxy, so the harness received tool calls as text; resolved by
  the local proxy (entry above). `setup.sh doctor` adds a structured
  tool-call check.
- First device run (Linux, Nix 2.35.2): environment built, Lean and plugin
  installed, doctor 12/14 (harness pin; endpoint, before the changes above).
- Checked on the VM (no Nix): dry runs of every preset; the generated config
  parsed by `aiprover config`; locks against the pins.

## 2026-10-10: Interactive captain skill

- Plugin `orchestration_plugin/` (marketplace
  `aiprover-orchestration-local`, skill `aiprover-captain`), packaged as
  the AIProver plugin is; `setup.sh` links the repository at
  `~/.config/aiprover_orchestration/root` and reinstalls the plugin.
- `scripts/env.sh` of the skill: repository paths and recording functions
  (`trace_start`, `trace_step`, `traced_check`, `traced_job`,
  `traced_subagent`, `forbidden`, `informal_build`), reusing
  `ALGORITHM`, `LEAN_ENVIRONMENT`, the role prompts, `forbidden_constructs`
  and `InformalBuilder`.
- `Trace.load` reads `trace.jsonl` (`Trace.from_lines`); `trace_view`
  loads through it.
- Model endpoint as in the AIProver plugin: `orchestration_plugin/aiprover.toml`
  (default `ssh` mode, handoff file of the Vista server job, ControlMaster
  opened by hand), linked at `~/.config/aiprover_orchestration/aiprover.toml`;
  `setup.sh` steps `deps` (AIProver's `setup.sh deps` with this config),
  `claude`, `doctor` (`--full`, without `--agents`, which calls `claude -p`).
- Checked: header, Lean check, AIProver job and decision steps recorded
  and rendered; informalization typeset; module statement check built.

## 2026-10-10: Hosted model spend

- Claude spend over the 30 traces in `results/` ($86.4): captain
  hand-backs 34%, sketches 22.5%, formalization and judge 13.5%,
  reviewer and writer about 20%. Output, mostly adaptive thinking at
  `effort: high`, is about 60%. Hand-backs recurse through splits
  (lemma generations up to 3).
- Refusals are a field of `Completion` set by every backend (Claude,
  OpenAI-compatible `refusal` / `content_filter`). A refused
  call is not retried and raises `AgentRefusal`; outside a hand-back the
  run ends with `error_kind: refused`, which is not resumed automatically.
- The call budget (`max_claude_calls`) counts billed calls to hosted
  models on any backend: successful calls and refusals.
- Hand-backs: `max_handbacks` per run (served config: 12); a lemma from a
  split is handed back after `aiprover_handback_after` × (1 + generation)
  failures; `last_attempts` keeps the two most complete samples, 6,000
  characters each.
- The hand-back prompt begins with the run-stable context (definitions,
  preamble, informal proof, main proof) for provider prefix caching.
- Per-phase agents: an agent specification's `phases` maps phase-label
  prefixes to option overrides (`build_agent`, `Agent.for_phase`). Our
  runs carry the server option `captain_phases`, which applies
  `configs/orchestrator/captain_phases.json` to a Claude captain: judge on
  Sonnet 5.5 at medium effort, formalize at medium effort, hand-back
  output limited to 32K tokens; sketch on Opus at high effort. Other
  runs use the selected model in every phase.

## 2026-10-10: Hosted captains, trace repair, Lake commands refused

- Without a lemma concurrency limit, the prove phase runs every lemma's
  jobs at once, including lemmas added by a split (`MAX_LEMMA_JOBS`); its
  thread pool had been sized by the lemmas pending when the phase began,
  so split lemmas waited for a running lemma's remaining attempts
  (332: `mc_cut_real_pointwise`; 048: three `away_final_transfer_*`).
  332 was restarted at 18:41 and runs both halves of the split.
  A run set back to `queued` before the worker has registered its
  exit is overwritten as `finished`; requeue after the exit is logged.

- Lemmas from a split are handed back after `aiprover_handback_after`
  failures, as other lemmas are. The former threshold of
  `aiprover_handback_after` times (1 + split depth) exceeded
  `aiprover_attempts_per_lemma` at depth 1 (6 > 5), so a split lemma ran
  out of attempts without reaching the captain.

- `aiprover resume` on a job whose workers are alive returns the job name,
  and the stage waits on it. A job can outlive the run that submitted it;
  the resumed run had counted such a job as a failed attempt and started a
  second one (332, `mc_cut_real_pointwise`, 21:18).

- 2026-10-10 23:00: 332 (metric Markov cotype of L1) proved: 49 lemmas,
  final review faithful, 41.6 h wall clock over 10 resumptions. Both
  halves of the `mc_cut_real` split were proved by the captain on
  hand-back after their AIProver attempts (`mono` 21:22, `pointwise`
  22:49).

- Run accounting: `benchmark.run_costs` gives hosted calls by role, model
  and purpose, and the solver's jobs, sessions, session-hours and GPU-hours
  (attributed by session slots: a server's GPUs over
  `aiprover_session_slots`; GPU groups carry `gpu` and `gpus`). It feeds
  trace.html (cost and compute panels), the report's summary table and
  benchmark.json. Summary totals (`calls_by_role`, `usage_by_model`) count
  the calls the trace keeps; the Claude budget still counts refusals.
  Sessions cut off by a lost server and malformed tool-call names are not
  counted as sessions or tools.
- `orchestrator.trace --drop-jobs` removes AIProver jobs that failed on the
  infrastructure, with their Lean checks and decisions. 332: the 11 jobs
  of the Mathlib rebuild window and 3 jobs of the earlier `mc_cut_real`
  split removed, with three resume calls that found their job still
  running; the previous trace is in `temp/trace_backup/`.
- The report's pdfTeX output is decoded with replacement: 332's report
  stopped at 22:59 on a byte sequence split by pdfTeX's line wrapping,
  leaving a single-pass PDF with unresolved references and no
  informalization; regenerated by resuming the run.
- The replay's assembly node shows the last assembly, not the first.
- 2026-10-11 01:01: 011 (large totient fibers, conditional on (H)) proved,
  final review faithful. Its informalization had no PDF: the writer
  closed the body with `\end{body>`, the body parsed as empty, and the
  repairs, which receive no Lean source, could not restore it.
  `ReportText.parse` now reads an unclosed body to the end of the reply
  (`open_body`); `reports.informal --reuse-draft` reuses the writer's
  draft from the trace. The informal check sees the Lean source but not
  the problem statement, and judged the conditional theorem weaker than
  the source; the three passages it caused were corrected in
  `informal_parts.json` and recompiled (`--rebuild`).

- `max_parallel` 56 (12:50) brought free memory to 4.7 GB at 51 sessions
  (15:21; about 2.4 GB per session plus 4.7 GB for the Mathlib index).
  Lowered to 48; `temp/hold_slots.py` holds slots 48 to 55 so that workers
  started under 56 cannot take them (stop it once those workers have
  ended). At 15:26 free memory reached 2.9 GB before any session had
  ended; the Mathlib index service (4.3 GB) was stopped and restarts once
  15 GB are free.

- A proof kept across a replan is kept only with the lemmas it uses
  (`structures.missing_lemmas`, in `replan` and in `restore_state`). 332's
  replan (step 428) kept `mc_cut_real`, proved by a split into
  `mc_cut_real_pointwise`, which the new sketch dropped; with 46 of 47
  lemmas proved, assembly failed on the unknown identifier (13:54).
  `mc_cut_real` is open again and 332 was requeued (13:57).

- Session slots are reassigned during a run: every minute, while no lemma
  waits to start a job, running jobs grow by the slots freed meanwhile
  (`rebalance` in `stages/aiprover.py`; `aiprover grow <job> -k N`, whose
  sessions end at the job's deadline; `aiprover slots`; `status --json`).
  A job may have several worker processes; the last one writes its result.
  Growth is bounded by the free slots of the machine as well: on
  2026-10-10 at 12:40 all 44 (`max_parallel`) were held and 16 sessions
  waited.

- Hosted models (`Agent.hosted`: the Claude backends, and an
  OpenAI-compatible agent with a `provider`) receive the carried notes
  without the sessions' reasoning (`knowledge.without_reasoning`). The
  captain's hand-back prompts carried it, and Opus's safeguards refused
  17 of 72 hand-backs of 332 and 048 (`[reasoning_extraction]`), each
  billed at about $0.4 to $0.6 with no reply. The last refused prompt,
  resent without the reasoning, was answered.
- A Lean check of a hand-back reply that times out stops the run as an
  infrastructure failure instead of rejecting the reply.
- `orchestrator.trace` repairs stopped runs: `--rewind N` (`rewound`)
  drops the steps from N on and the AIProver jobs still running at N, and
  `--refused-handbacks` drops refused hand-backs, so that a resume hands
  those lemmas back again.
- 048 rewound to step 254 (01:13, before the Mathlib rebuild; no proof
  lost); 332's seven refused hand-backs removed. Both requeued. The
  previous traces are in `temp/trace_backup/`.
- The harness refuses `lake build|exe|update|clean` (`lake_refusal`).
- Calls refused by a provider's safety classifier are left out of the
  trace (`trace.is_untraced`, `without_untraced_calls`; the call log keeps
  them), on resume, in shared copies and through the repair command. `share_results` skips `.tmp`
  files, which are traces being rewritten.

## 2026-10-09: Knowledge carry, bounded subagents, DGX session limit

- `orchestrator/knowledge.py`: a failed session is condensed into a note
  (ending, Mathlib searches with those that found nothing, files read, last
  reasoning, code); a lemma carries its newest notes (6,000 characters) to
  later jobs through the harness's `--hint-file`. The notes are recorded in
  `solver_budget_exhausted` and restored on resume.
- Hand-back: the captain sees the carried notes and may replace or clear
  them (`<knowledge>`) with any action; a retry lets the captain review the
  lemma again after `aiprover_handback_after` further failures. A lemma with
  notes but no code is no longer deferred.
- Harness: subagents (`explore`) bounded together by 20 % of the session's
  time limit and 30 % of its turn budget, and by the hard fence; past them
  every subagent call is refused with an instruction to report, and no new
  subagent starts. The task text states that `grep` is ripgrep (`a|b`;
  `a\|b` matches a literal `|`): 10 of the first 20 searches in one 048 session
  found nothing for this reason.
- GPU group `dgx` for runs on the persistent server: no Slurm job or
  handoff; the worker starts its runs only while the endpoint answers. 048
  moved there (it held a place in group `openai`); the DGX vLLM refused
  connections 09:55-10:05 and returned with about 32 requests of another
  client in flight.
- DGX sessions: 10,800 s (`SOLVER_TIMEOUTS`, `dgx.toml`); the DGX completes a
  median 11 turns per hour (8 sessions) against 76 on Vista (89 sessions).
- Self-contained result directories: a resumed run takes its config
  (unless `--config` is given) and its problem from the trace, and its
  library from `results/<run>/library.lean`, a snapshot taken at the start;
  without a call log, the Claude budget is counted from the trace's model
  calls; an AIProver job missing from the work directory is submitted
  afresh; `Trace.load` reads `trace.json.gz`.
- `utils/share_results.py` (timer `aiprover_share_results`, every 15 min):
  the latest run of each OpenAI problem is copied to
  `~/workspace/orchestration-results` (`completed_problems/` once finished,
  else `ip_problems/`), with the trace gzipped, the config as `config.json`
  and a README index with resume instructions.
- Group `open` paused: its server jobs cancelled, and `main` and `openai`
  no longer borrow it. DGX load test requested by its owners: 048 with 32
  session slots (32 concurrent requests); `max_parallel` 44 in all harness
  configs (sessions share one slot pool on the VM, about 2.5 GB each).
- Decision: no lemma concurrency limit in served runs
  (`aiprover_lemma_concurrency` 0): every open lemma runs, and the session
  slots, which scale with the model servers, bound the work.
- Page, "Model servers": per group, the requests in flight through its proxy,
  healthy servers of its pool, and the groups it borrows from or lends to;
  the persistent DGX shown by host as an always-on server.
- Library search, experimental (`docs/proposed/mathlib_search.md`):
  run-level Mathlib map in every job's guidance (`knowledge.MathlibMap`,
  rebuilt from the trace); harness search fence (12 searches without an edit
  or Lean check); stated `sorry` gaps listed in session notes; hand-back on
  exhaustion with a retry granting `aiprover_handback_after` further
  attempts. Session notes now match tool results to calls by tool name and
  grep pattern (results had been paired with the wrong calls).
- A captain hand-back refused by the model's safety classifier no longer
  stops the run: the lemma continues without it (decision action
  `refused`, restored on resume). Markov's `mc_cube_energy` hand-back was
  refused at 18:43 and 21:11, each time ending the run for a resumption.
- Semantic Mathlib index, experimental (`aiprover_orchestration/search/`,
  service `aiprover_mathlib_index` on 127.0.0.1:18570): 259,660 declarations
  of the pinned Mathlib, 247,785 with LeanSearch v2 descriptions; embeddings
  (Qwen3-Embedding-0.6B) computed on Vista (job 1062259, gh-dev); harness
  configs set `leanfinder_url`, and `lean_leanfinder` is described as the
  first search. The installed lean_lsp_mcp skips its client rate limit for a
  self-hosted Lean Finder (`tools/search.py`, original kept as `.orig`).
- Decision: index text from LeanSearch v2's v4.28 corpus joined by name to
  the v4.23 declarations, rather than informalizing v4.23 anew; 5% of
  declarations (renamed or removed by v4.28) keep docstring and type only.
- First hour after the relaunch (22:21-23:43, 69 sessions, mostly resumed
  with their original instructions): 10 `lean_leanfinder` queries against
  916 greps; the search fence fired 53 times. The fence message now points
  to `lean_leanfinder` before grep.

- 2026-10-10 13:26: 011, stopped at 12:52 to pick up `max_parallel` 56
  (an interrupted run is not requeued by the worker), requeued in group
  `openai`; main's server jobs had been released at 13:23 after 30 min
  without runs of its own. Main's tunnel was closed at 12:33 as "ended job
  1063220" while that job was running: an unreadable handoff file (a failed
  remote read) counted as naming no running job. A tunnel to a running job
  is now kept when the handoff cannot be read (`_tunnel_current`).
- 2026-10-10 14:27: 048, stopped at 13:38 to resume on the day's changes
  (budget 47 calls), requeued. 332 proved all 47 lemmas; assembly failed at
  13:54 (`mc_cut_real_pointwise` unknown: a proof reused after the replan
  depends on a split lemma of the earlier sketch that was never proved) and
  was relaunched at 13:57; it is reproving `mc_cut_real`.
- 2026-10-10 15:41: 332 limits raised for our run (`max_claude_calls` 60,
  `max_handbacks` 30); its trace lost the split of `mc_cut_real` and the
  captain's proof that used the never-proved `mc_cut_real_pointwise`
  (steps 203-204; backup in `temp/trace_backup/`), so that the lemma could
  be handed back again. Resumed: the captain split it into
  `mc_cut_real_mono` and `mc_cut_real_pointwise` at 15:43.
- 2026-10-10 20:15: 332 has 46 of its 47 sketch lemmas proved; open is
  `mc_cut_real`, through its split lemmas `mc_cut_real_mono` (job 3/5, 10
  sessions) and `mc_cut_real_pointwise` (job 1/5, 7 sessions), then
  assembly. One session of each ended without
  a proof (turn limit).
- Trace pages show AIProver jobs in flight (`live`, read from the job
  directories at render time: per session its state, model replies so far,
  and once ended its status and turns): a "Jobs in progress" panel in the
  solver chapter and a count in the overview. The query server re-renders
  running runs every 5 min (`LIVE_PAGE_REFRESH_SECONDS`) as well as on new
  trace steps; query server restarted at 20:14.

## 2026-10-09: Decode speed of the Vista server

- Diagnosis (job 1058807): eager mode (no CUDA graphs, no torch.compile),
  untuned Triton FP8 MoE kernels (vLLM default configuration for
  E=128, N=2048 on GH200), no speculative decoding. With ~6B active
  parameters per token, 34 ms per decode step against ~2 ms of weight
  reads, and per-request speed independent of batch size, place the
  bottleneck in kernel launches and host overhead.
- `scripts/vista/vllm_common.sh` (environment, Ray cluster, `vllm serve`
  arguments) and `vllm_args.sh` (CUDA graphs, n-gram speculation, KV-cache
  type, attention backend) factored out of `serve_aiprover_vista.sbatch`;
  defaults `ENFORCE_EAGER=0`, `GPU_MEMORY_UTILIZATION=0.93`.
- `tune_moe_vista.sbatch`, `bench_serve_vista.sbatch` (arms A-G, four
  loads), `make_edit_dataset.py` (40 rewrites of session Lean files),
  `summarize_bench.py`; `benchmark.json` reports speculative acceptance
  (`spec_acceptance_rate`, `spec_tokens_per_step`).
- Stampede3: `scripts/stampede3/serve_aiprover_s3.sbatch` (one node, TP
  over its GPUs, `serve_aiprover.sh` with the settings of `vllm_args.sh`),
  `configs/aiprover/s3.toml`, `s3_logged.toml`,
  `aiprover_reasoning_proxy_s3.service` (ports 18560, 18570).
- Stampede3 facts: `$WORK = /work2/11757/loganluna/stampede3`, `$SCRATCH =
  /scratch/11757/loganluna` (separate from Vista's); `/work` (checkpoint,
  `aiprover_serve/scripts`) is readable from Stampede3. GPU partitions:
  `h100` (24 nodes, 4 x H100 each) and `pvc`; no H200 partition. vLLM
  0.27.1 in `$WORK/venvs/vllm-0.27.1` (pip, CUDA 13 wheels).

## 2026-10-09: Proxy tool-call parsing; server pools; prompt termination

- The persistent server (vLLM 0.31.0, context 262,144 since 2026-10-08)
  runs without a tool-call parser: tool calls arrive as text in the
  Mistral format (`[TOOL_CALLS]name[ARGS]{...}`). The reasoning proxy now
  converts such text into structured `tool_calls` (streamed and
  non-streamed replies, parallel calls, ids in Mistral's 9-character form),
  so sessions do not depend on the server's parser; replies already parsed
  pass unchanged.
- SIGTERM to a run (`DELETE /api/runs/<id>`) sets `agents.aiprover.
  STOPPING`: pending AIProver jobs are cancelled within one poll interval
  (60 s) and no new job starts.
- `srv_20261008_233359_OpenAIMath_048_LipmanZariskiCounterexample` paused
  (cancelled) in its prove stage; its 42 DGX sessions made no tool calls.
  Restarted as `srv_20261009_001341_OpenAIMath_048_LipmanZariskiCounterexample`,
  forked at the prove stage (formalization, audit and sketch kept).
- Server pools: group `openai` borrows the server of `main`; its proxy pools
  both tunnels and the run's session slots scale with the healthy servers.
  A run of group `main` cancelled to free `main`'s server (job 1057849) for
  `OpenAIMath_332_MetricMarkovCotypeL1`, which uses the `openai` server as
  well once job 1059513 starts.
- VM at 16 sessions: 31 GB used (about 2 GB per session; Mathlib .olean
  files are shared), load about 2 on 32 cores. `max_parallel` raised to 28.
- Session memory (Lean v4.23.0, Mathlib): the language server's watchdog
  loads the reference index of every `.ilean` on the search path (213 MB on
  disk, about 1.0 GB in memory with `LEAN_SRC_PATH` set; there is no option
  to skip it); the file worker importing Mathlib holds about 0.37 GB private,
  the .olean files (4.6 GB) being memory-mapped and shared. A search path
  mirrored without `.ilean` files gives 0.71 GB per server instead of
  1.72 GB, with the same diagnostics and go-to-definition into Mathlib.
- Tactic blocks (`<main_proof>`, `<proof>`) keep the indentation of their
  first line (`extract_tactics`); stripping it had misaligned every
  multi-line block. `OpenAIMath_332` failed its sketch on this and was
  relaunched as `srv_20261009_010221_OpenAIMath_332_MetricMarkovCotypeL1`
  (forked at the sketch stage); 011 and 164 paused and requeued behind it.
- Tunnels: the worker points a group's tunnel at the server job its handoff
  names once that server answers, closes a tunnel whose handoff job has
  ended, and cancels a superseded running job it submitted; a busy group
  keeps one successor server job queued. Group `main`'s tunnel had stayed on
  job 1059338 while its own job 1057849 ran (and failed at 00:58).
- Group `main` (submissions from the page) borrows `openai` and `open`:
  its proxy (18565) pools tunnels 18555, 18559 and 18558, so the page is
  served while `main`'s own job waits in the gh queue.
- Group `openai` borrows `main` and `open`. Overnight watcher
  (`temp/overnight/watch.py`, unit `aiprover_overnight_watch`): events in
  `temp/overnight/events.log`, and a 2 h gh-dev job kept queued for `open`
  until a 12 h gh job serves it.
- Group `open`'s handoff named its ended job 1059258 while job 1059338 (of
  `main`) served the same node and port; 1059338 was renamed
  `aiprover_srv_open` and the handoff rewritten. The worker now requires the
  handoff's job to be one of the group's running jobs and rechecks the
  servers of running runs every 5 min.

## 2026-10-08: Benchmark logging; 048 on the DGX server

- `orchestrator/benchmark.py`: every run samples the solver endpoint's vLLM
  metrics and this machine's load every 30 s (`metrics.jsonl`) and writes
  `benchmark.json` at its end (server tokens, throughput, running and
  waiting requests, KV-cache use, mean context length, queue time, time to
  first token, request latency, preemptions, prefix-cache hit rate;
  AIProver sessions and their turns; model calls and Lean checks by role;
  machine load). The worker rewrites it when a served run ends;
  `python -m aiprover_orchestration.orchestrator.benchmark results/<run_id>`
  rewrites it by hand.
- Reasoning proxy: each turn record carries `prompt_tokens` (context
  length), `seconds` and `in_flight` (requests being forwarded).
- Worker: a queued run that needs no model server starts when an earlier
  run of its group waits for its Vista server; agents of a persistent
  version (`dgx`) keep their own endpoint in every GPU group.
- `OpenAIMath_048_LipmanZariskiCounterexample` (estimated quickest) moved
  from the Vista queue to the persistent DGX server as
  `srv_20261008_233359_OpenAIMath_048_LipmanZariskiCounterexample`, with
  the complex-run settings and 4 AIProver sessions; it is the endpoint's
  only client, so the server metrics describe this run.

## 2026-10-08: OpenAI math runs in GPU group `openai`

- GPU group `openai` with its own Vista server job (`aiprover_srv_openai`,
  handoff `aiprover_server_openai.txt`), tunnel 18559 and reasoning proxy
  18569 (`configs/aiprover/vista_openai.toml`,
  `vista_logged_openai.toml`, unit `aiprover_reasoning_proxy_openai`).
- The five problems of `data/openai_math.jsonl` queued in bank order (332,
  011, 164, 048, 259) with these settings: Opus
  5.5 (high) captain, reviewer and writer, Sonnet 5.5 auditor, trained
  AIProver solver, 5 attempts per lemma, 2 replans, 30 Claude calls. Server
  job 1059513 submitted (pending).

## 2026-10-08: OpenAI math problem bank

- Source: openai/math at `fd4aeeb` (719 manuscripts, 372 families). 242
  families have a Lean scope note or catalogued main result; 130 families
  (244 manuscripts) have neither.
- `data/openai_math.jsonl`: five rows in the dataset schema plus
  `raw_source_path`. Statements are self-contained; proofs are the source
  TeX (332, 011 Section 8, 164 Corollary 1.2, 259) or the source proof
  outline (048).
- `docs/openai_math_problems.md`: per family the OpenAI summary,
  manuscripts with abstracts and citations, verbatim main results,
  assessment (proof structure, external inputs, Mathlib notes, OpenAI Lean
  status) and the bibliography.
- Undocumented Lean in the release covers parts of families 201 (Kurosh,
  conditional on its Lifting Theorem) and 138 (the low-space Subset Sum
  companion); both were excluded.

## 2026-10-08: Resume fallback

- `AIProverAgent.supports_resume` probes the installed CLI for `aiprover
  resume`. Without it, a resumable job is logged as not resumable and its
  lemma gets a new job.

## 2026-10-08: Model backends over provider APIs

- `agents/claude.py`: backend `claude` over the Anthropic Messages API
  (`ANTHROPIC_API_KEY`), streamed, adaptive thinking, `effort` in
  `output_config`; refusal fallback (`fallbacks: "default"`) for the models
  that accept it; cost from list prices.
- `openai_compatible`: `provider` presets `openai`, `huggingface`,
  `openrouter` (base URL and key variable); hosted providers keep their own
  sampling defaults; OpenAI requests carry `max_completion_tokens`.
- Query server: hosted providers selectable per role with a model id.
- Run configs and recorded results name the Claude backend `claude`.

## 2026-10-08: Reply cap, empty replies, target check, one process per run

- Reasoning proxy: reply cap 16,384 tokens (about 9.5 min at the measured
  29 tokens/s per request), below the 720 s read timeout of the harness's
  HTTP client; a reply that outlasts that timeout is abandoned and
  regenerated by the client. Past assistant messages without content or tool
  calls (replies capped during reasoning) receive placeholder content, since
  vLLM rejects (400) a history that holds them.
- Both Vista servers measured: 29 tokens/s per request independent of the
  number of concurrent requests (1 to 6), no queueing or preemption, KV cache
  at most 30 % with 6 requests. 8 concurrent sessions per group do not limit
  throughput.
- AIProver grading (`aiprover.py`, both plugin variants): a compiling answer
  that does not reproduce the target statement verbatim is `rejected`, not
  `verified`. Replayed over 80 recorded verified samples: 4 change to
  rejected, each also rejected by the orchestrator's extraction; every sample
  the orchestrator accepted stays verified.
- Query server: a queued run starts only when no orchestrator process of the
  run is alive, so a resume never shares the trace with a run that is still
  shutting down.
- AIProver sessions ended by an infrastructure fault (lost endpoint, protocol
  rejection) resume their saved session after 30 s, up to 3 times, unless the
  answer already verifies; a session that still ends on the fault is
  reported as `infra`, which `aiprover resume` continues; a job whose
  sessions all end so is not counted as an attempt. vLLM's rejection of an empty
  assistant message counts as a protocol rejection.
- Reasoning proxy: invalid tool names are sanitized in tool results as well
  as in tool calls; reasoning is recorded in the session directory named by
  the request's absolute path, so one proxy serves jobs of any workspace.
- Query server: Vista server jobs are released after 30 min without work
  in their group (`QUERY_SERVER_IDLE_SECONDS`, 0 = kept until their wall
  time).
- Migration completed: both running runs interrupted, `results/`,
  `logs/`, `temp/`, `libraries/`, `aiprover/work/jobs/` and the server state
  copied, absolute paths of the job database and of the five interrupted
  AIProver jobs rewritten to this repository, and the runs resumed by this
  repository's query server.
- An AIProver job with any session cut off by infrastructure or a
  cancellation is not counted as an attempt; the run stops as an
  infrastructure failure and its resume continues those sessions.
- Trace pages of running runs: the query server renders `trace.html` and
  `trace_replay.html` whenever the trace has steps the pages lack, at most
  once a minute per run, so a run can be followed through the server while
  in progress; pages are replaced atomically.

## 2026-10-08: Repository restructured as one package

- One Python package, `aiprover_orchestration/`, with subpackages `lean/`
  (checker, module layout, source parsing), `agents/` (base, Claude,
  OpenAI-compatible, AIProver, pool), `library/`, `orchestrator/`,
  `server/`, `roles/` and `utils/`; repository locations in `paths.py`.
- Orchestrator: `pipeline.py` holds the run state, shared helpers and
  driver; each stage is a module of `stages/` (formalize, sketch, prove,
  aiprover, assemble, review). Prompts are Markdown files in
  `prompts/system/` and `prompts/templates/`. Proof extraction from AIProver
  answers in `extraction.py`; report and informalization in `reports/`;
  trace pages in `trace_view/`.
- Configurations in `configs/` (`orchestrator/*.json`, `aiprover/*.toml`,
  `server/tokens.json`); databases in `state/`; scripts in
  `scripts/{systemd,vista,local}/`.
- Entry points: `python -m aiprover_orchestration.orchestrator.run`,
  `uvicorn aiprover_orchestration.server.app:app`,
  `python -m aiprover_orchestration.library`.
- The AIProver agent reads its job directory from the configuration's
  `work_root`; a forked run whose source has no call log starts with an
  empty one.
- Code formatted to 80 columns (black); string literals of prompts, LaTeX
  and SQL are kept as they are.
- Checked against the previous code: identical prompts, trace pages of two
  runs, proof extraction on 312 recorded AIProver samples, declaration
  splitting, and resume state of 21 traces; a fork of a proved run at
  `assemble` reproduces the verified file.

## 2026-10-08: Repository migrated to `aiprover_orchestration`

- Orchestrator, query server, scripts, AIProver configurations, data,
  libraries, analyses and results moved from `aiprover_prove2me_workspace`
  (archived); AIProver is a submodule at `5d71cc5`.
- The orchestrator uses the library's Lean workspace, Lean checker, locked
  module build, Claude access and auditor prompt.
- AIProver venvs and ripgrep provisioned under `aiprover/` with the
  submodule's `setup.sh`; configurations and systemd units point to the new
  repository.

## 2026-10-08: Theorem library

- Library of definitions and theorems over its own Lean workspace
  `orchestration_workspace/` (Lean v4.23.0, Mathlib v4.23.0, packages linked
  from `~/workspace/lean_projects/TmpProjDir`).
- `core/`: Lean checks and locked module builds, module layout, SQLite
  store, and `Library` (publish, verify, resolve).
- `interface/`: CLI and FastAPI router; `prompts/`: role prompts and agent
  guide; `agents/claude.py`: Claude access; `utils/smoke.py`:
  end-to-end test with Claude Haiku as solver.

- Smoke test with Claude Haiku 4.5 passed: definition and theorem
  published, ill-typed theorem rejected; `sorry`, wrong-type and
  self-importing proofs rejected; two theorems proved by Haiku in two
  attempts each (each first reply had no Lean code); a sketch accepted with
  one open leaf, its parent resolved once the leaf was proved; graph served
  over HTTP. 3 min wall time.

## 2026-10-06: Work log

- No reply reached the proxy's token cap.
- Lemmas waiting on a hand-back are no longer counted in the session split.

## 2026-10-06: Final review, report, replay spacing

- Stages `review` and `report` after a verified assembly
  (`orchestrator/pipeline.py`, `orchestrator/report.py`). Roles `reviewer`
  and `writer`, served as `claude-opus-5-5` with high effort, each a fresh
  agent with its own system prompt. Both decisions are restored on resume
  for the same solution (SHA-256 of the file), so a resumed run does not
  repeat them. `final_claude_calls` (8) is added to the call budget.
- Report: the writer's text names declarations with `\LeanDecl{name}`; the
  generator inserts the declaration from the verified file (with its doc
  comment), highlighted by Pygments, and its credit from the trace. Checks
  before compiling: every declaration placed exactly once, no file, shell or
  preamble commands. pdfLaTeX, since LuaTeX's font loader is not installed
  on the VM; Unicode symbols of the Lean code are declared as LaTeX symbols
  (unmapped ones are boxed and reported). Two repair rounds on LaTeX errors.
- Replay: a lemma column is as wide as the most sessions of its own jobs;
  jobs of more than 4 sessions wrap into offset rows. Review and report
  nodes follow the verification node.
- Query server: `report.pdf` and `report.tex` among a run's files.

## 2026-10-06: Work log

- Submitted through the query server with the defaults (Opus 5.5
  orchestrator, reviewer and writer; trained AIProver model), 5 attempts per
  lemma, 2 replans, 16 Claude calls.

## 2026-10-06: Libraries of verified results

- `orchestrator/library.py`; `Config.library`, `Config.extend_library`.
  A run given a library formalizes its statement against the library's
  definitions (`FORMALIZE_LIBRARY_TEMPLATE`); the library text is the prefix
  of the definitions, so lemma checks, solver contexts and the final file
  contain it. `formalization_compiled` records the library and its length;
  the run's contribution is the text after that prefix. A library is checked
  to compile without `sorry` at the start of a run. Reports cite library
  declarations and do not present them.
- Query server: `library`, `library_text`, `extend_library` and `after` on
  submission; `GET /api/libraries`; the form selects or uploads a library.
  The worker starts a run with `after` only once that run has ended.

## 2026-10-06: Persistent AIProver server

- `cic-dgx-01.tacc.cloud:8040`: vLLM 0.30.0, served name `aip-leanstral`,
  context 131,072, weights `/opt/share/gits/Agentic/TACC-vllm-jobscript/
  models/hf--aip-leanstral` (on that host). Same tokenizer as the Vista
  checkpoint (identical token ids on a Lean test string).
- Throughput, idle server: 34.7 tok/s decode at short context, ~13 tok/s at
  15k context, prefill ~3,200 tok/s; aggregate ~21 tok/s at 4 and at 8
  parallel requests (15k-token prompts). Served runs use 4 sessions
  (`aiprover_session_slots` 4).
- The reasoning proxy forwards to it (`--upstream`), names the served model
  (`--model aip-leanstral`), caps replies at 16,384 tokens, and lowers
  `max_tokens` so prompt and reply fit the context (prompt counted by the
  server's `/tokenize`). The harness config takes the served name from
  `/v1/models` (`model = ""`). Runs on the trained version need no Vista job.
- A job whose sessions all fail on infrastructure is an infrastructure
  failure of the run (resumed, not counted as an attempt). A run with
  `after` starts only once that run has finished.

## 2026-10-05: Session sharing and hand-back to the captain

- `aiprover_session_slots` (served: 8): sessions per AIProver job are
  `free * w / (w + sum of rivals' w)`, at least 1, where `w = 1 + failed
  attempts` and the rivals are the unproved lemmas that could start
  alongside it (up to `aiprover_lemma_concurrency`). A mocked run of two
  lemmas gave 6 and 2 sessions, then 8 to the last one.
  A lemma waiting on a hand-back is not a rival; if its hand-back fails
  with no free slot, its job takes 1 session above the limit.
- `aiprover_handback_after` (served: 3): before a lemma's next job, once per
  statement, the captain receives the sketch, the informal proof and the
  failed attempts and chooses: prove (helpers and proof checked by Lean),
  split (new lemmas placed before it and handed to solvers, with a proof of
  it from them), restate (the sketch must compile with the new statement;
  refused if proved lemmas use it), or retry. One repair on Lean errors. A
  hand-back is deferred when no session left code (setup failure) and
  skipped when fewer than 2 Claude calls remain. `lemma_handback` decisions
  are restored on resume.
- Captain: `effort = "high"`, `max_output_tokens = 128000`.
- Pages: jobs with different session counts, hand-back nodes in the lemma's
  column, split lemmas hanging from their hand-back, captain proofs as
  winners.

## 2026-10-05: Replay per sketch; stalled AIProver replies

- The replay draws one tree per accepted sketch, stacked downward: the
  sketch fans out left to right into its lemmas, and the attempts on each
  lemma stack downward beneath it (AIProver: a job block with its sessions
  below, each later job under the previous one; chat solvers: one lane per
  chain). A replan starts the next tree below the previous one; lemmas it
  keeps appear as single nodes, and the edge to the replan runs down the
  left of the previous tree.
- Chat-solver Lean checks are matched to the latest call with the same
  lemma, worker and round; checks of later sketches had been attributed to
  the first sketch's calls.
- The reasoning proxy sets `max_tokens = 24576` on chat requests without
  one. Of 10,823 recorded replies the 99.9th percentile is 57,000
  characters (~16,000 tokens) and the longest 190,000; the cap takes about
  14 min at 29 tokens/s. A capped reply without a tool call ends the
  agent's turn and the harness's answer check returns the Lean errors (up
  to 3 times per session). Capped replies are marked `truncated` in
  `reasoning.jsonl` and in the replay's Reasoning tab.

## 2026-10-05: Attempts per lemma; Opus captain

- `aiprover_attempts_per_lemma` (default 1): a lemma is retried by new
  AIProver jobs until proved or until that many jobs on its statement ran to
  completion. Jobs stopped by a lost server or an interruption do not count.
  Counts are restored from the trace on resume, so a resumption does not
  re-run a lemma whose attempts are used up. Replay label: retry.
- Served config: captain Opus 5.5 (auditor Sonnet 5.5).
- Per-run settings in the server (`options` column, JSON).
- An AIProver answer's helper declarations are spliced under the answer's
  `open` commands, as its kept theorem already was.

## 2026-10-05: Resumptions without limit; interrupted jobs out of the pages

- A served run that loses its model server is requeued and resumed without a
  limit on resumptions. It ends after 3 resumptions in a row that add no
  durable progress (formalization, verdict, sketch, proved lemma, replan),
  counted from the trace (`progress`, `stalled` columns of `jobs`).
- The trace pages leave out AIProver jobs stopped by an interruption: jobs
  with neither a proved lemma nor a recorded failure, or with every session
  cancelled, when the run later resumed. Their Lean checks are left out too;
  the later job on the lemma takes their place and steps are renumbered.
  `trace.json` keeps every step.

## 2026-10-05: Failed attempts in the replan

- The replan prompt now gives, for each failed lemma, the solvers' last code
  besides the Lean errors. For AIProver, each sample's own declarations
  (helpers and its version of the lemma, comments kept) with the Lean errors
  located in them, ordered by: some proof written, fewer errors, fewer
  `sorry`. Samples that left only `sorry` are omitted. Chat solvers give
  their last helpers and proof.
- The harness's sample status is not shown to the captain: it counts the
  `sorry` stubs of earlier lemmas in the sample's file.
- `solver_budget_exhausted` decisions record the errors and attempts;
  resuming restores them, so a replan after an interruption sees them.
- AIProver jobs themselves are unchanged (no feedback from earlier jobs).

## 2026-10-05: Replay after a replan

- Each lemma node in the replay appears with the first accepted sketch that
  names it and hangs from that sketch. A revised sketch adds an edge,
  labelled re-delegated, to each unproved lemma it restates; reused and new
  lemmas are drawn as before.
- AIProver attempts in the replay are drawn as one block per job: a job node
  (start, duration, outcome) over its parallel sessions. Jobs on a lemma
  follow one another top to bottom; the edge into each later job names why it
  was started (resumed, replan, new job).

## 2026-10-04: Work log

- Solver `max_turns` 100 -> 200 (clock 5400 s): in the first sketch of
  a run, 12 of 22 failed samples stopped at the 100-turn limit, most with
  clock time left.
- `wrapped_lemma_proof`: if an AIProver proof does not check under the
  sketch's statement, the answer's whole theorem is kept as helper
  `<lemma>_aiprover_s<k>` (under its `open` commands) and the lemma is proved
  by `apply`.

## 2026-10-04: Concurrent served runs

- The query server runs up to two approved runs at once
  (`QUERY_SERVER_MAX_RUNS`), in submission order, never two of the same
  problem; runs adopted after a restart are followed by pid without blocking.
- AIProver `max_parallel` 8 -> 16 (machine-wide flock slots); the VM has 32
  cores and 125 GB, with 108 GB available under 8 sessions. The trained model
  served 8 concurrent requests at 28 tokens/s each; 16 is not yet measured.
- The final `lake build` of a run holds `prove2me_workspace/.lake_build.lock`.

## 2026-10-04: Work log

- The query server offers every JSONL file in `data/`; run ids of
  non-JiatuBook problems carry the whole problem id.

## 2026-10-04: AIProver sessions, Lean returns and reasoning in the replay

- The replay's step drawer, for an AIProver solver box, has the tabs Reply
  (the sample's final file), Prompt, System prompt, Lean returns (the
  harness's check of the final file with Lean's diagnostics, and the
  pipeline's check of the extracted proof), Session (the agent's transcript:
  messages, tool calls with their arguments, tool results) and Reasoning.
  Chat calls with a Lean check gain a Lean returns tab. The separate
  `trace_lean.html` page is removed.
- The model's reasoning is produced (`thinking = "high"`) but discarded by the
  harness's agent: vLLM returns it as `reasoning`, vibe reads
  `reasoning_content`. `server/reasoning_proxy.py` forwards between the
  harness and the tunnel without changes and records each completion's
  reasoning in the session's `reasoning.jsonl`; served runs use it through
  `aiprover/aiprover_vista_logged.toml`. Runs before it have no reasoning;
  the replay says so.
- The trace stores per sample the transcript (messages cut to 8,000
  characters, tool text to 6,000) and the reasoning; older traces are
  completed from `aiprover/work/jobs/` when rendered. These records are
  embedded in the replay only.

## 2026-10-04: Served settings for the 000000 retry

- Solver `max_turns` 60 → 100 (clock unchanged at 5400 s).
- Model server jobs on `gh`, 12 h (unit environment): the 2 h `gh-dev`
  limit ended sessions mid-lemma in both trained-model runs.

## 2026-10-04: Sketch cap removed; AIProver attempts in the trace pages

- `max_sketch_lemmas` and its prompt removed: with the instruction to prefer
  fewer, larger lemmas, the first sketch of `000000` had 2 lemmas, neither
  proved in 5400 s; the smaller lemmas of the replanned sketch were the ones
  proved.
- Trace pages: an AIProver call is one job of several samples. Its Lean
  checks and `lemma_proved` decision are attributed to the job recorded last
  before them on the same lemma (all jobs share worker 0, round 0, and the
  decision carries the sample index). Arrows between jobs read `resumed`,
  `replan` or `new job` instead of `errors`; a job without an extractable
  proof shows each sample's ending (`sorry`, `does not compile`, `no answer`,
  `server lost`) in a neutral colour.
- Each AIProver sample is drawn as its own solver lane (replay, graph,
  walkthrough timeline) with its own ending, turns, Lean check and timing.
- AIProver sample endings in the trained-model runs (30 samples): 3
  verified; 11 stopped at the 60-turn limit after 20-55 min; 12 reached the
  5400 s clock; 6 lost the server at a job's end; 1 had a tool call rejected
  by vLLM (400).

## 2026-10-04: Served runs with the trained model

- `srv_20261003_232555_000004` (`prop: bounding ite base`): proved and
  verified (axioms `propext`), 1627 s wall; sketch of 1 lemma
  (`itr_eps_var`), proved by sample 0 in 14 turns (1092 s); sample 1 ended
  `sorry` at 1472 s and held the job until then. Calls captain 3, auditor 1,
  solver 1; 0.19 USD. Base model: 23 turns, 1557 s, 36 min over three
  invocations.
- `srv_20261003_232550_000000` (`lmm: Fp subset F`): formalization
  FAITHFUL; sketch of 2 lemmas, neither proved in 5400 s; replan to 5
  lemmas; `lemmas_unproved` after 5.4 h and two server-job resumptions.
  Proved: `rec_length_arith`, `cobhamF_length_bound` (the latter on its third
  attempt, in a resumed pass); failed: `cobhamF_append`,
  `cobhamF_smash_bound`, `cobhamF'_rec_bound`. Calls captain 4, auditor 1,
  solver 15.
- GPU use: server jobs 1046962, 1047196, 1047408 at their 2 h limit (12
  node-hours); launch checks 0.8 node-hours.

## 2026-10-03: Launch check of the trained FP8 model

- Job 1046940 (`gh-dev`, 2 nodes, PP=2): weights 56.25 GiB per GPU in 52 s,
  KV cache 2,433,264 tokens, `doctor` 15/15 PASS. Decode 29 tokens/s per
  request, flat to 28 tokens/s at 8 concurrent requests (223 tokens/s
  aggregate); the base model gave 14 tokens/s single and about 12-15 at 8.
- Startup failures resolved in `serve_aiprover_vista.sbatch`: Ray's memory
  monitor killed rank 0 at 96% node memory (HBM counts toward node memory on
  GH200), now disabled with an 8 GiB object store; the kernel OOM-killed
  rank 0 while FlashInfer compiled its CUTLASS FP8 MoE kernels (one nvcc per
  core, ~4 GB each); vLLM's CUTLASS FP8 MoE is disabled for this scheme;
  `--moe-backend triton` starts cleanly.

## 2026-10-03: Served-run solver settings

- `Config.aiprover_lemma_concurrency` (AIProver lemma jobs in flight; 0 =
  all): after a failure, lemmas not yet started are skipped, the flag being
  set by the failing thread before its worker is reused.
- Served config: lemma concurrency 1, at most 5 lemmas, solver timeout
  5400 s. Checked with stub captain and solver.
- Lemma concurrency raised to 4 after the launch check (8 sessions at
  28 tokens/s each with the trained model).

## 2026-10-03: Trained AIProver model; submodule on main

- `AIProver` submodule updated to `origin/main` (`5d71cc5`: setup.sh
  lean-lsp-mcp patch fix, `serve/` scripts) and set to track `main`
  (`branch = main` in `.gitmodules`).
- Trained model `/work/11428/pjana/aiprover_model`: Mistral native format,
  FP8 e4m3, 112 GB. `serve_aiprover_vista.sbatch` detects `params.json` and
  serves it with the flags of `AIProver_plugin/serve/`: Mistral config and
  loader, PP across the nodes (TP=1), 1,048,576-token context, no offload,
  node-local compile caches. HF checkpoints keep the previous path.
- Query server and docs default to the trained model; scripts copied to
  `$WORK/aiprover_serve/scripts` on Vista.
- First served run `srv_20261003_142941_000000` (`lmm: Fp subset F`, base
  model): formalization FAITHFUL, sketch of 5 lemmas, none proved within
  1800 s (8 concurrent AIProver sessions at about 15 tokens/s each); replan
  to 17 lemmas, 4 proved; `lemmas_unproved` after two server-job
  resumptions.

## 2026-10-03: Approval and on-demand Vista server

- Submissions await approval by a token holder; approved runs queue.
- `server/vista.py`: the worker submits a `gh-dev` model server job when an
  approved run waits and no `aiprover_srv` job is queued or running, and
  cancels the jobs it submitted after 15 min without runs.
- A lost AIProver endpoint after a lemma attempt raises `AgentCallError`
  (`Orchestration._prove_with_aiprover`), so the run stops as an infrastructure
  failure instead of replanning; the worker requeues it and resumes it on
  the next server job (at most 3 times). Served runs use `--max-restarts 0`.
- `QUERY_SERVER_AUTH=off` for testing: no token for submission and viewing.
- Checks with stub orchestrator and stub Vista: approval (403 without
  token), one submission per demand, resumption after a lost server, idle
  cancellation.

## 2026-10-03: Query server

- `server/` (FastAPI, uvicorn): token-authenticated web page and JSON API on
  the VM for submitting a JiatuBook UUID or a free-text statement and proof,
  following the progress log, and retrieving the trace pages and standalone
  Lean file. Runs are `orchestrator.run` subprocesses with
  `config_aiprover_vista_served.json`.
- `Config.max_claude_calls` (`AgentPool`): budget on `claude` calls per
  run, counted across resumptions; set to 10 for served runs.
- Checks: API, ownership, queueing, cancellation, adoption of a live run and
  resumption of a lost run after a server restart (stub orchestrator); budget
  stop with the real orchestrator at `--max-claude-calls 1`
  (`failed`, `budget`, one captain call).

## 2026-10-02: AIProver server on Vista, launch checks

- Container `$WORK/containers/vllm-gh200.sif`: vLLM 0.27.1, torch 2.13.0
  (cu130). Two-node Ray cluster and TP=2 workers form; MLA attention backend
  `FLASH_ATTN_MLA` is selected without overrides.
- Server arguments for the Leanstral checkpoints: `--tokenizer-mode mistral`
  (tekken.json only), `--config-format hf --load-format safetensors`, and
  `--limit-mm-per-prompt '{"image": 0}'` (text-only mode; the Pixtral
  processor is not profiled).
- vLLM's deepseek_v2 loader reads per-expert tensors only. The fused
  checkpoints (`leanstral_hf_fused`, `leanstral_tiny_fused`) are converted by
  `scripts/unpack_experts_vista.sbatch` (CPU `gg` node, apptainer) into
  `$SCRATCH/aiprover_ckpt/leanstral_{tiny,base}_unpacked`; config.json and
  tokenizer files are copied unchanged.
- Base checkpoint served on 2 `gh-dev` nodes (job 1044066): 68.5 GiB of
  weights per GPU with 42 GiB per rank offloaded, load 325 s, KV cache
  559k tokens (12 GiB per GPU); single-request decode about 14 tokens/s.
  `doctor` 15/15 PASS through the tunnel.
- `structures.file_scoped`: `open X in` / `set_option ... in` in a
  statement preamble become file-level commands, since the preamble precedes
  every lemma and `solution` in sketches and solutions; applied on parse and
  on resume.
- Run `vista_smoke_000004` (Sonnet 5.5 captain and auditor, AIProver base
  solvers): formalization FAITHFUL in round 0; sketch with one lemma
  (`itr_eps_all`) after the preamble fix; `itr_eps_all` proved by the
  AIProver base model (both samples verified; sample 1 in 23 turns, 1557 s).
  Solution verified: compiles, no `sorry`, no axioms, module build matches
  the theorem. Wall time 2159 s over three invocations; calls captain 11,
  auditor 1, solver 1.

## 2026-10-02: Deployment for the AIProver model on Vista

- Repository `aiprover_prove2me_workspace` with the AIProver submodule
  (`PrithwishJana/AIProver` at `e36b1c7`) provisioned on the orchestration VM:
  Lean project `~/workspace/lean_projects/TmpProjDir` (Lean v4.23.0, Mathlib
  `37df177`, REPL, cslib `cd368e6`), `aiprover/venv_vibe`, `aiprover/venv_mcp`
  (three lean-lsp-mcp patches), ripgrep; `doctor` 13/14 PASS on both
  configurations, the endpoint row pending the server.
- `prove2me_workspace/`: clone of `prove2me/prove2me_workspace` with the same
  requires and manifest as the AIProver project and its `.lake/packages`
  linked; `Solutions.SmokeTest` builds in 4.5 s.
- JiatuBook validation set copied to `data/` (from PartitionAndProve
  `feature/SAM`); `run.py` defaults to it.
- `scripts/serve_aiprover_vista.sbatch` and `submit_aiprover_vista.sh`: Ray
  cluster across `gh` nodes in `$WORK/containers/vllm-gh200.sif`, vLLM tensor
  parallel over the nodes (default 2), Mistral tool-call and reasoning
  parsers, handoff file `$SCRATCH/servers/aiprover_server.txt`, server restart
  inside the allocation.
- `aiprover/aiprover_vista.toml` (ssh mode over `~/.ssh/vista.sock`, handoff
  followed) and `orchestrator/config_aiprover_vista.json` (captain
  `claude-opus-5-5`, auditor `claude-haiku-4-5`, solvers AIProver at 4
  samples, 100 turns, 5400 s). Captain and auditor probes answer.
- `orchestrator/config_aiprover_vista_smoke.json`: smoke configuration with
  captain and auditor `claude-sonnet-5-5` and AIProver solvers on the base
  checkpoint `/work/11428/pjana/leanstral_hf_fused` (222.4 GiB bf16, fused
  experts): 2 samples, 60 turns, 1800 s per rollout.

## 2026-10-02: AIProver harness as solver backend

- AIProver (`../PartitionAndProve/AIProver_plugin`, champion harness
  `d01_r04`, mistral-vibe 2.24.2, lean-lsp-mcp 0.30.0) provisioned against
  `aiprover/aiprover.toml`: direct endpoint at the local vLLM server, venvs,
  ripgrep and job directory under `aiprover/`, Lean project v4.23.0. `doctor`:
  12/13 PASS; the reasoning check fails by design (non-reasoning model).
- `orchestrator/aiprover_agent.py`: `aiprover` backend; one job per lemma with
  `workers` samples, fixed context (definitions, earlier lemmas) and fixed
  statement; each sample's proof is extracted and passed through the pipeline's
  Lean gate. Configuration `config_aiprover.json` (Haiku captain, Qwen auditor,
  Qwen through AIProver).
- Serving for the harness: fp8 weights and KV cache, tool calling (`hermes`),
  28,672-token context at 0.565 GPU memory utilization alongside other GPU
  applications. vibe's compaction threshold is lowered to 18,000 tokens through
  `VIBE_MODELS` (environment layer above the harness's config file); the
  harness bytes are unchanged.
- Run `aiprover_000004_haiku_qwen` (stopped after 3569 s, resumable):
  formalization FAITHFUL in audit round 1; first AIProver job (82 turns, 2
  samples) ended in `sorry` and timeout; replan produced 8 lemmas; stopped
  during their AIProver jobs. The run was also interrupted once by the tool
  time limit and resumed from step 22 (pending replan).

## 2026-10-01: Unsolved problem 000020 (`thm: ind multiple var`)

- Target: two-variable structural induction in PV; no system in the
  JiatuBook evaluation produced a semantically correct proof (16 produced
  type-correct output).
- Run `local_000020` (captain Sonnet 5, auditor and solvers Qwen3-4B):
  formalization compiled at the first attempt and was judged FAITHFUL; it
  encodes PV functions as variable-free combinators (`PVFunc n` with `proj`,
  `comp`, `srec`) and omits the length bound of limited recursion. Sketch with
  three lemmas (`congr_app_two`, `itr_self_trim`, `key_diag`); no lemma proved
  in the first round. Cancelled during the first replan.
- Run `jiatu_000020_sonnet_haiku`: forked from `local_000020` at the prove
  stage (`--fork-from`), solvers Haiku 4.5. `congr_app_two` proved at the
  first attempt; `itr_self_trim` unproved by 3 of 4 chains when cancelled
  (outcome `interrupted`, resumable).
- `run.py --fork-from <run> --fork-stage <stage>` starts a new run from the
  steps of another run before the given stage.

## 2026-10-01: Resumable runs

- `orchestrator/resume.py` rebuilds the state of a run from the decision
  steps of its trace (formalization, audit verdicts, sketches, proved lemmas,
  replans); `--resume --run-id <id>` continues from the last completed step.
  The trace is extended in place, its clock continues, and each resumption is
  listed under `resumptions`. Call and usage totals are re-read from
  `calls.jsonl`.
- Model calls that fail after all retries raise `AgentCallError`
  (`error_kind: infrastructure`); `run.py` resumes such runs automatically
  (`--max-restarts`, `--restart-delay`). SIGTERM unwinds the run so the
  outcome is recorded.
- Verified on copies of `smoke_000004` truncated before assembly (resumed with
  no model calls, verified) and with one lemma unproved (3 solver calls,
  verified).

## 2026-09-30: Pluggable subagents; local open-weights solvers

- `orchestrator/agents.py`: `Agent` interface with backends `claude`,
  `openai_compatible` and `python` (custom class by import path);
  `AgentPool` routes calls by role, retries transient failures and records
  each call in `calls.jsonl` and `trace.json` (fields `backend`, `model`,
  `usage`). Roles are configured in `config.json` (captain on Claude,
  auditor and solvers local) and `config_claude.json` (all Claude).
- Local serving: `.venv_serve` (uv, Python 3.12, vLLM 0.30.0, torch
  2.13.0+cu132), `scripts/serve_local.sh`; Qwen3-4B-Instruct-2507 in bf16,
  max length 24576, GPU memory utilization 0.84, KV cache 26.8k tokens.
- Run `local_000004` (captain Sonnet 5, auditor and solvers Qwen3-4B):
  formalization compiled at the first attempt, audit FAITHFUL, sketch with one
  lemma (`ITR_eps_var_eq_eps`, structural induction); 60 solver calls over
  three sketches produced no compiling proof. Status `lemmas_unproved`,
  963 s.

## 2026-09-29: Trace walkthrough page

- `orchestrator/trace_view.py` renders any `trace.json` into
  `results/<run_id>/trace.html`: ten chapters (overview, problem, algorithm
  flow chart, roles and prompts, formalization, blind audit, sketch, solver
  timeline, verification, filterable step log) and a drawer with the prompt,
  reply, Lean source and compiler errors of each step.
- `trace_graph.html`, rendered alongside: pan/zoom graph with the captain as
  root, subtasks (formalize, audit, verdict, sketch, one node per lemma,
  assemble), their agents, and attempt chains with repair edges and lemma
  dependencies; subtasks fold, nodes open the same step records.
- `trace_replay.html`: top-to-bottom animation on the trace clock (input →
  captain → formalize/audit/verdict/sketch → lemma fan-out → solver lanes by
  round → winning proofs → assembly → verified proof). Play/pause, 5–60×
  speed, scrubber, event stepping, narration, event log, auto-following
  camera; the full graph is shown faintly before playback.
- Replay "Export JPG": renders the completed tree (end-of-run state, header
  with run id, status, duration, models) at 2× to a JPEG; saved through the
  artifact `downloads` capability when published, a browser download when
  opened locally.

## 2026-09-29: Sonnet→Haiku smoke test on JiatuBook 000004

- Environment: `prove2me_workspace` pinned to Lean v4.23.0 / Mathlib `37df177`
  (the JiatuBook evaluation environment).
- Target: `JiatuBook_BoundedArithmetic_000004` (`prop: bounding ite base`),
  solved in the benchmark by aristotle, AIProver, Numina and Opus-5.
- Run `smoke_000004`: formalization compiled at the first attempt and was
  judged FAITHFUL from the Haiku read-back; the sketch had three lemmas
  (`concat_eps_subst`, `ITR_eps_var_eps`, `ITR_eps_subst`); all were proved
  by Haiku solvers (19 solver calls). Final solution verified: compiles, no
  `sorry`, axioms `[propext]`, type equal to the target theorem.
- Wall time 787 s; notional cost 0.60 USD (Sonnet) + 1.43 USD (Haiku).
- Full trace: `results/smoke_000004/trace.json`.
