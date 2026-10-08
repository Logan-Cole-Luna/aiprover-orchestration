# Logbook

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

## Todo

- Migrate the orchestrator (pipeline, agents, AIProver backend, libraries of
  verified results, trace pages, query server) onto this library.
- Project upload with our own Lean meta-programs.
- Authentication for the HTTP API.
- Missions and milestones; several pinned Lean environments.

## Done

- Theorem library with CLI and HTTP API; Haiku smoke test passed.

## Decisions

- One SQLite database per workspace (`data/library.db`); solution modules
  are named by target and submission id (`Sol_<theorem>_<id>`), so
  submissions of different theorems never share a module.
- Theorem statements import only Mathlib and definitions; a disproof
  imports no theorems.
- A sketch may depend on `sorryAx` only through imported library theorems;
  its own text is free of forbidden constructs.
- Resolution recomputes the least fixed point of the decomposition graph
  after every submission.

## Issues
