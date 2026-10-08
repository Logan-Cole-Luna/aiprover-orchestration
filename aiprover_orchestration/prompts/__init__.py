"""Role prompts: captain, auditor, solver, and the agent guide (skill)."""

from pathlib import Path


def load(role: str) -> str:
    return (Path(__file__).parent / f"{role}.md").read_text()
