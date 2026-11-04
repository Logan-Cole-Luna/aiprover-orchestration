"""Soundness of the fidelity chain: the verdict rule of the judges and the
isolation of the reference formalization from the run's state."""

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace

from aiprover_orchestration.agents.base import BudgetExhausted
from aiprover_orchestration.orchestrator import fidelity
from aiprover_orchestration.orchestrator.stages.formalize import FormalizeStage
from aiprover_orchestration.orchestrator.structures import Formalization


def verdict(word: str) -> str:
    return f"<verdict>{word}</verdict><issues>{word} issues</issues>"


@dataclass
class ScriptedAgents:
    replies: dict[str, str]
    agents: dict = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.agents = dict.fromkeys(self.replies)

    def complete(self, prompt: str, role: str, **options) -> str:
        self.calls.append(role)
        reply = self.replies[role]
        if isinstance(reply, Exception):
            raise reply
        return reply


class Stage(FormalizeStage):
    """Formalization stage whose Lean checks answer by label."""

    def __init__(self, replies: dict, proved: tuple[str, ...] = ()):
        self.agents = ScriptedAgents(replies)
        self.proved = proved
        self.config = SimpleNamespace(
            fidelity_probes=True, max_audit_rounds=0, max_repairs=0
        )
        self.row = {"informal_statement": "S", "informal_proof": "P"}
        self.library = ""
        self.library_path = ""
        self.slug = "target"
        self.auditor_system = ""
        self.reference = None
        self.reference_attempted = False
        self.events = []

    def _check(self, label, text, **fields):
        return SimpleNamespace(
            ok=label in self.proved,
            errors=[],
            axioms={f"Conclusion.{self.slug}": [fidelity.PREMISE_AXIOM]},
        )

    def _standalone(self, form, body):
        return body

    def _own_definitions(self, form):
        return form.definitions

    def _decision(self, event, **fields):
        self.events.append(event)


FORM = Formalization("", "", "target", " : True ")
PANEL = {"auditor": "read-back", "captain": verdict("FAITHFUL")}


class VerdictRule(unittest.TestCase):

    def audited(self, stage: Stage) -> Formalization:
        return stage.formalize(form=Formalization(**vars(FORM)))

    def test_vacuous_statement_reaches_no_model(self):
        stage = Stage(PANEL, proved=("fidelity_vacuous",))
        self.assertEqual(self.audited(stage).verdict, "REVISE")
        self.assertEqual(stage.agents.calls, [])

    def test_every_judge_must_accept(self):
        for judge, captain, expected in (
            ("REVISE", "FAITHFUL", "REVISE"),
            ("FAITHFUL", "REVISE", "REVISE"),
            ("FAITHFUL", "FAITHFUL", "FAITHFUL"),
        ):
            stage = Stage(
                {
                    "auditor": "read-back",
                    "judge": verdict(judge),
                    "captain": verdict(captain),
                }
            )
            self.assertEqual(self.audited(stage).verdict, expected)

    def test_unparsable_verdict_is_not_faithful(self):
        stage = Stage({**PANEL, "judge": "no tags"})
        self.assertFalse(self.audited(stage).verdict.startswith("FAITHFUL"))
        self.assertNotIn("captain", stage.agents.calls)

    def test_reference_never_enters_the_run_state(self):
        reply = (
            "<definitions></definitions>"
            "<statement>theorem target : True := by sorry</statement>"
        )
        stage = Stage(
            {**PANEL, "formalizer": reply},
            proved=("formalize", "fidelity_implies_reference"),
        )
        form = self.audited(stage)
        self.assertNotIn("formalization_compiled", stage.events)
        self.assertIn("reference_compiled", stage.events)
        self.assertIn("the statement implies it: True", form.evidence)
        self.assertIn("it implies the statement: False", form.evidence)

    def test_budget_stop_is_not_a_missing_reference(self):
        stage = Stage({**PANEL, "formalizer": BudgetExhausted("spent")})
        with self.assertRaises(BudgetExhausted):
            self.audited(stage)


class Evidence(unittest.TestCase):

    def test_equivalence_needs_both_directions(self):
        one_way = fidelity.Evidence(
            implies_reference=True, implied_by_reference=False
        )
        self.assertFalse(one_way.equivalent)
        self.assertFalse(fidelity.Evidence().equivalent)

    def test_proof_without_the_premise_relates_nothing(self):
        def result(*axioms):
            return SimpleNamespace(ok=True, axioms={"Conclusion.t": axioms})

        self.assertTrue(
            fidelity.used_premise(result(fidelity.PREMISE_AXIOM), "t")
        )
        self.assertFalse(fidelity.used_premise(result("propext"), "t"))

    def test_counterexample_comes_from_the_errors(self):
        message = "error: Found a counter-example!\nn := 2"
        self.assertEqual(
            fidelity.counterexample_in(["other", message]), message
        )
        self.assertEqual(fidelity.counterexample_in(["other"]), "")


if __name__ == "__main__":
    unittest.main()
