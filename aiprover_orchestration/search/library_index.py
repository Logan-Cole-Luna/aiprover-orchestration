"""Semantic index of a package library (orchestrator/libraries.py), served
beside the Mathlib index (server.py, `POST /library/<name>`).

    python -m aiprover_orchestration.search.library_index libraries/<name>

Each declaration of the package's modules becomes a document: its full name,
statement with the proof elided, and docstring. Embedded on the CPU, which
suffices for a library of thousands of declarations; written to
`<package>/index/`.
"""

import json
import re
import sys
from pathlib import Path

import numpy as np

from ..lean.text import declarations, statements_only
from ..orchestrator import libraries
from ..paths import DATA_DIR
from .embed import Embedder

MODEL = DATA_DIR / "mathlib_index" / "qwen3-embedding-0.6b"
BATCH = 32
SCOPE = re.compile(r"^(namespace|end)\s+([\w.]+)\s*$|^(section)\b|^end\s*$", re.M)
DOCSTRING = re.compile(r"/--(.*?)-/", re.S)


def namespaces(text: str) -> list[tuple[int, str]]:
    """(line, namespace) from each `namespace` or `end` command on."""
    stack, changes = [], []
    for match in SCOPE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if match.group(1) == "namespace":
            stack.append(match.group(2))
        elif match.group(3) or match.group(0).strip() == "end":
            stack.append(None)  # a section, closed by a bare `end`
        elif stack:
            stack.pop()
        changes.append((line, ".".join(n for n in stack if n)))
    return changes


def documents(package: str) -> list[dict]:
    found = []
    for module, file in libraries.modules(package):
        text = file.read_text()
        scopes = namespaces(text)
        view = statements_only(text)
        lines = text.splitlines()
        for kind, name, line in declarations(text):
            if not name:
                continue
            scope = next((s for at, s in reversed(scopes) if at < line), "")
            full = f"{scope}.{name}" if scope and not name.startswith("_root_.") else name
            head = "\n".join(lines[max(0, line - 12) : line - 1])
            doc = DOCSTRING.findall(head)
            statement = next(
                (block for block in view.split("\n\n") if f" {name}" in block),
                f"{kind} {name}",
            ).replace(" := by sorry", "")
            found.append(
                {
                    "formal_name": full,
                    "informal_name": "",
                    "kind": kind,
                    "type": statement[:1500],
                    "informal_description": doc[-1].strip() if doc else "",
                    "path": module,
                    "text": f"{(doc[-1].strip() + chr(10)) if doc else ''}{full} : {statement}"[:2000],
                }
            )
    return found


def build(package: str) -> Path:
    folder = libraries.resolve(package) / "index"
    folder.mkdir(exist_ok=True)
    entries = documents(package)
    embedder = Embedder(str(MODEL))
    vectors = np.zeros((len(entries), 1024), dtype=np.float16)
    for start in range(0, len(entries), BATCH):
        texts = [entry["text"] for entry in entries[start : start + BATCH]]
        vectors[start : start + len(texts)] = embedder(texts)
    with open(folder / "documents.jsonl", "w") as out:
        for entry in entries:
            out.write(json.dumps(entry) + "\n")
    np.save(folder / "embeddings.npy", vectors)
    return folder


if __name__ == "__main__":
    print(build(sys.argv[1]))
