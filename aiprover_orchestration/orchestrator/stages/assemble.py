"""Assembly and verification of the final solution.

The proved lemmas are spliced into the sketch and the standalone file must
compile without `sorry` and with the standard axioms only. The definitions,
target and solution are then written as library modules; a check module
importing target and solution proves `example : type_of% @target :=
@solution`, so the solution's type is the target's without the solution
importing the target.
"""

from ...lean import checker
from ...lean.layout import module_name, module_path
from ..config import MAIN_NAME
from ..structures import Formalization, Sketch

VERIFICATION_CHECKS = (
    "compiles",
    "no_sorry",
    "standard_axioms_only",
    "module_build",
    "statement_matches",
)


class AssembleStage:

    def assemble(self, form: Formalization, sketch: Sketch) -> tuple[str, dict]:
        body = self._sketch_body(form, sketch, use_proofs=True)
        standalone = self._standalone(form, body)
        result = self._check(
            "final", standalone + f"\n#print axioms {MAIN_NAME}\n"
        )
        extra_axioms = checker.nonstandard_axioms(result, MAIN_NAME)
        checks = {
            "compiles": result.ok,
            "no_sorry": not result.has_sorry_warning,
            "standard_axioms_only": extra_axioms == [],
            "axioms": result.axioms.get(MAIN_NAME),
            "errors": result.errors,
        }
        checks.update(self._verify_modules(form, body))
        checks["verified"] = all(checks[key] for key in VERIFICATION_CHECKS)
        self._decision("final_verification", checks=checks, solution=standalone)
        return standalone, checks

    def _verify_modules(self, form: Formalization, body: str) -> dict:
        """Write the run's modules and check the solution against the
        target; returns the `module_build` and `statement_matches` checks."""
        def_module = module_name("definition", self.slug)
        thm_module = module_name("theorem", self.slug)
        sol_module = module_name("solution", self.slug)
        check_module = module_name("check", self.slug)
        files = {
            def_module: f"import Mathlib\n\n{form.definitions}\n",
            thm_module: (
                f"import {def_module}\n\n{form.preamble}\n\n"
                f"{form.statement}\n"
            ),
            sol_module: f"import {def_module}\n\n{form.preamble}\n\n{body}\n",
            check_module: (
                f"import {thm_module}\nimport {sol_module}\n\n"
                f"{form.preamble}\n\n"
                f"example : type_of% @{form.theorem_name} := "
                f"@{MAIN_NAME}\n\n"
                f"#print axioms {MAIN_NAME}\n"
            ),
        }
        for module, text in files.items():
            (self.result_dir / module_path(module).name).write_text(text)
        result = checker.build(
            self.workspace,
            {module_path(module): text for module, text in files.items()},
            check_module,
            timeout=self.config.lean_timeout * 2,
        )
        (self.result_dir / "module_build.log").write_text(result.output)
        solution_imports_target = thm_module in files[sol_module]
        return {
            "module_build": result.ok,
            "statement_matches": result.ok and not solution_imports_target,
        }
