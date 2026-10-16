"""Writing files that other processes read while they change."""

import os
import threading
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temporary file in its directory, so
    that a reader sees the old or the new content, never a partial one."""
    path = Path(path)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(text)
    os.replace(temporary, path)
