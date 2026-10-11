# Lean gates

Every proposal passes its gate before it changes the run, as in the
pipeline (`docs/orchestration.md` §5.1). All files start with the header

```lean
import Mathlib
set_option autoImplicit false
```

## Forbidden constructs

`forbidden FILE` lists the constructs of `lean/checker.py`
(`FORBIDDEN_PATTERNS`) present outside comments: `sorry`, `admit`, `axiom`,
`opaque`, `implemented_by`, `extern`, `native_decide`,
`macro`/`syntax`/`elab`, `run_cmd`, `debug.skipKernelTC`. In a file with
lemma stubs, `sorry` is expected and is the only construct allowed. In
definitions, in accepted proofs and in the final file, nothing is allowed.

## Gate per stage

| Stage | File | Gate |
|---|---|---|
| Formalize | header, definitions, `theorem <slug> ... := by sorry` | `traced_check ... --statement-only` PASS; `$AIP probe` without `COUNTEREXAMPLE`; definitions free of forbidden constructs |
| Sketch | header, definitions, lemma stubs, `theorem solution` | `traced_check ... --statement-only` PASS; no `sorry` in the text of `solution`; names unique and not `solution`; `probe` without `COUNTEREXAMPLE` on the lemmas |
| Lemma sample | AIProver's answer | status `verified`, `fixed-code:kept`, no forbidden construct in the declarations the answer adds |
| Hand-back `proof`, `split` | context + helpers + the lemma with its proof | `traced_check ... --statement-only` PASS (earlier lemmas are stubs); the lemma's own proof has no `sorry` |
| Hand-back `restate`, replan | the whole sketch | as for the sketch |
| Assemble | `<slug>_standalone.lean` | `traced_check` PASS without `--statement-only` (compiles, no `sorry`, standard axioms `propext`, `Classical.choice`, `Quot.sound` only), then the module check below |

`probe` results: `COUNTEREXAMPLE` means the statement is false as written.
`closed by automation alone` on a statement that should carry content
suggests a narrowed statement; check it against the source. `untestable` and
`no counterexample found` prove nothing.

## Module statement check

The standalone file proves `solution`; the module check shows that its type
is exactly the target's, without `solution` importing the target. It writes
four modules of the Lake project `$WS`, named as in `lean/layout.py` (`M` =
the theorem name with `.` and `'` replaced by `_`):

| Module | File | Content |
|---|---|---|
| `Definitions.Def_M` | `$WS/Definitions/Def_M.lean` | `import Mathlib`, the definitions |
| `Theorems.Thm_M` | `$WS/Theorems/Thm_M.lean` | `import Definitions.Def_M`, any `open` lines, the target `theorem <slug> ... := by sorry` |
| `Solutions.Sol_M` | `$WS/Solutions/Sol_M.lean` | `import Definitions.Def_M`, any `open` lines, helpers, lemmas with proofs, `solution` |
| `Solutions.Check_M` | `$WS/Solutions/Check_M.lean` | `import Theorems.Thm_M`, `import Solutions.Sol_M`, any `open` lines, `example : type_of% @<slug> := @solution`, `#print axioms solution` |

`autoImplicit` is off for the whole project (`lakefile.lean`), so the module
files omit the `set_option`. Build under the workspace lock that the
pipeline also takes:

```bash
. <skill>/scripts/env.sh
(cd "$WS" && flock .lake_build.lock lake build Solutions.Check_M) \
    > "$ROOT/results/$RUN/module_build.log" 2>&1; echo "rc=$?"
cp "$WS"/{Definitions/Def_M,Theorems/Thm_M,Solutions/Sol_M,Solutions/Check_M}.lean "$ROOT/results/$RUN/"
```

`module_build` holds when the build succeeds; `statement_matches` holds when
it succeeds and `Sol_M` does not import `Theorems.Thm_M`. The build runs on
the orchestration VM; the first build of a new module takes a few minutes.

## Final verification record

`final_verification` carries `checks` with the fields of
`stages/assemble.py` (`VERIFICATION_CHECKS`): `compiles`, `no_sorry`,
`standard_axioms_only`, `axioms`, `errors`, `module_build`,
`statement_matches`, and `verified` (all five checks hold).
