"""An AIProver phase over the stand-in CLI (`fake_aiprover.py`), run as its
own process with the orchestrator's signal handlers, for the termination
test. Usage: python -m tests.terminate_child <directory>"""

import signal
import sys
from pathlib import Path

from aiprover_orchestration.agents.aiprover import AIProverAgent
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.run import terminate
from aiprover_orchestration.orchestrator.structures import Lemma, Sketch
from tests.support import FORM, ScriptedPool, orchestration


def main(directory: Path) -> None:
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, terminate)
    solver = AIProverAgent(
        "fake",
        config=str(directory / "aiprover.toml"),
        cli=str(directory / "bin" / "aiprover"),
        poll_seconds=1,
    )
    run = orchestration(
        directory,
        ScriptedPool(solver=solver, budget=0),
        Config(aiprover_handback_after=0),
    )
    lemmas = [
        Lemma("lem_a", "theorem lem_a : True"),
        Lemma("lem_b", "theorem lem_b : 1 = 1"),
    ]
    try:
        run._prove_with_aiprover(
            FORM, Sketch(lemmas, "trivial"), list(enumerate(lemmas))
        )
    finally:
        (directory / "unwound").touch()
        run.trace.finish({})


if __name__ == "__main__":
    main(Path(sys.argv[1]))
