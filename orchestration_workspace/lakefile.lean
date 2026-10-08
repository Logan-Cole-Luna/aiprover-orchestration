import Lake
open Lake DSL

-- Lean v4.23.0 with Mathlib v4.23.0, matching the AIProver Lean project
-- (~/workspace/lean_projects/TmpProjDir), whose .lake/packages this project
-- links.
package «orchestration» where
  leanOptions := #[⟨`autoImplicit, false⟩]

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "v4.23.0"

require REPL from git
  "https://github.com/leanprover-community/repl.git" @ "2f8073af0a5e3a141fee075652790a2c19132516"

require cslib from git
  "https://github.com/leanprover/cslib" @ "cd368e67e7b5cd563be1d7dc47254e9c4d5962cf"

lean_lib «Definitions» where
lean_lib «Theorems» where
@[default_target]
lean_lib «Solutions» where
