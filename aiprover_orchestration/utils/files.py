"""Writing files that other processes read while they change."""

import json
import os
import threading
from pathlib import Path


def read_json(path: Path) -> dict:
    """The JSON object at `path`; empty if it is absent or unreadable."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def read_jsonl(path: Path) -> list[dict]:
    """The records of a JSON lines file, without malformed lines; empty if
    it is absent or unreadable."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temporary file in its directory, so
    that a reader sees the old or the new content, never a partial one."""
    path = Path(path)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(text)
    os.replace(temporary, path)
