"""Fidelity evidence that Lean certifies, without a model's judgment.

Probes elaborate variants of the formal statement: a proof of `False` from
its hypotheses certifies that it is vacuous, a proof by automation alone
shows that it carries little content, and a counterexample shows that it is
false. Equivalence compares the statement with an independent formalization
of the same source by proving each from the other, as BEq+ does (Poiroux et
al., EMNLP 2025), https://github.com/augustepoiroux/RLMEval. The premise is
left as `sorry`, so `sorryAx` among the axioms of a proof shows that the proof
used it; a true conclusion proved from the library alone relates nothing. A
failed proof establishes nothing, since automation is incomplete.
"""

from dataclasses import dataclass

# Closes a goal without the statement under test. `first` does not catch a
# heartbeat timeout, so `exact?` precedes the tactics that can exhaust it.
AUTOMATION = (
    "first | contradiction | rfl | exact? | decide | (simp_all; done) | omega"
    " | aesop"
)
PREMISE_AXIOM = "sorryAx"
COUNTEREXAMPLE_MARKER = "Found a counter-example"
EQUIVALENCE_TACTIC = """first
    | exact {premise} ..
    | exact?
    | (apply {premise} <;> first | assumption | (simp_all; done) | aesop)
    | (have premise := @{premise}; simp_all; done)
    | (convert {premise} using 2 <;> first | assumption | (simp_all; done))
    | (have premise := @{premise}; aesop)"""


@dataclass
class Evidence:
    vacuous: bool = False  # `False` proved from the hypotheses
    automatic: bool = False  # proved by automation alone
    counterexample: str = ""
    # Proved directions against the reference formalization; None without
    # a usable reference.
    implies_reference: bool | None = None
    implied_by_reference: bool | None = None

    @property
    def equivalent(self) -> bool:
        return bool(self.implies_reference and self.implied_by_reference)

    def report(self) -> str:
        """The evidence as the judge reads it."""
        lines = [
            "Hypotheses: "
            + (
                "Lean proved False from them, so the statement is vacuous."
                if self.vacuous
                else "no contradiction found (not a proof of consistency)."
            ),
            "Automation: "
            + (
                "closes the statement alone; check that it was not narrowed."
                if self.automatic
                else "does not close the statement."
            ),
        ]
        if self.counterexample:
            lines.append(f"Random testing found:\n{self.counterexample}")
        if self.implies_reference is not None:
            lines.append(
                "Independent formalization of the source by another model: "
                f"it implies the statement: {self.implied_by_reference}; "
                f"the statement implies it: {self.implies_reference}. "
                "A false entry means that automation found no proof."
            )
        return "\n".join(lines)


def vacuity_probe(name: str, signature: str) -> str:
    return (
        f"theorem {name}_vacuous{signature}:= by\n"
        f"  intros\n  exfalso\n  {AUTOMATION}"
    )


def automation_probe(name: str, signature: str) -> str:
    return f"theorem {name}_automatic{signature}:= by\n  intros\n  {AUTOMATION}"


def counterexample_probe(name: str, signature: str) -> str:
    return f"theorem {name}_tested{signature}:= by\n  plausible"


def counterexample_in(errors: list[str]) -> str:
    return next((e for e in errors if COUNTEREXAMPLE_MARKER in e), "")


def used_premise(result, conclusion_name: str) -> bool:
    """Whether a check of `implication_file` proved the conclusion from the
    premise, rather than from the library alone."""
    axioms = result.axioms.get(f"Conclusion.{conclusion_name}", [])
    return result.ok and PREMISE_AXIOM in axioms


def scoped(namespace: str, definitions: str, preamble: str, body: str) -> str:
    return "\n\n".join(
        filter(None, [f"namespace {namespace}", definitions, preamble, body])
    ) + f"\n\nend {namespace}"


def implication_file(
    premise: tuple[str, str, str, str],
    conclusion: tuple[str, str, str, str],
) -> str:
    """Lean text proving the `conclusion` statement from `premise`, each
    (definitions, preamble, name, signature), in separate namespaces so that
    equal names of the two formalizations do not clash."""
    premise_defs, premise_open, premise_name, premise_signature = premise
    defs, preamble, name, signature = conclusion
    tactic = EQUIVALENCE_TACTIC.format(premise=f"Premise.{premise_name}")
    return "\n\n".join(
        [
            scoped(
                "Premise",
                premise_defs,
                premise_open,
                f"theorem {premise_name}{premise_signature}:= by sorry",
            ),
            scoped(
                "Conclusion",
                defs,
                preamble,
                f"theorem {name}{signature}:= by\n  intros\n  {tactic}",
            ),
            f"#print axioms Conclusion.{name}",
        ]
    )
