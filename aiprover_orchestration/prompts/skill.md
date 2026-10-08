# Theorem library: agent guide

The library holds Lean 4 definitions and theorems (Lean v4.23.0, Mathlib
v4.23.0, `autoImplicit false`). Every item is a module of
`orchestration_workspace/`; every proof is verified locally before it is
recorded.

## Roles

- **Captain** (`captain.md`): formalizes a problem faithfully as definitions,
  a goal theorem and lemmas.
- **Auditor** (`auditor.md`): writes blind read-backs of Lean statements.
- **Solver** (`solver.md`): proves, disproves or decomposes open theorems.

## Items and modules

| Item | Module | Content |
|------|--------|---------|
| Definition `foo` | `Definitions.Def_foo` | Sorry-free Lean code |
| Theorem `foo` | `Theorems.Thm_foo` | `theorem foo ... := by sorry` |
| Submission `n` for `foo` | `Solutions.Sol_foo_n` | `theorem solution ...` |

A theorem's status is `Open`, `Proved` or `Disproved`. Definitions and
theorem statements import only Mathlib and definitions.

## Submissions

A submission proves (or, with `--disprove`, refutes) one theorem by
declaring `theorem solution` whose type is exactly the theorem's type (its
negation for a disproof). Gating rules:

1. A top-level `theorem solution` is declared.
2. The file never imports its own target theorem.
3. No `sorry`, `admit`, `axiom`, `native_decide` or other forbidden
   construct appears; only the axioms `propext`, `Classical.choice` and
   `Quot.sound` are used.
4. Imports are Mathlib and published items only; a disproof imports no
   theorems.

Outcomes: `ACCEPTED` (target proved), `SKETCH_ACCEPTED` (the proof imports
open theorems, which become its children; the target is proved
automatically once all children are), `REJECTED` (with Lean errors) or
`ERROR`.

## Commands

```
python -m aiprover_orchestration add-definition NAME FILE
python -m aiprover_orchestration add-theorem NAME FILE [--title T] [--statement S]
python -m aiprover_orchestration verify THEOREM FILE [--disprove] [--explanation E]
python -m aiprover_orchestration show NAME
python -m aiprover_orchestration search [--query Q] [--status S] [--kind K]
python -m aiprover_orchestration graph THEOREM
python -m aiprover_orchestration open-leaves THEOREM
```

The same operations are served under `/api/library` by
`uvicorn aiprover_orchestration.interface.api:app`.
