"""Limits on the work of one machine, across runs, servers, and users.

Runs of every user draw on the same slots: lock files in a shared directory,
held with `flock`, so that a process that ends gives its slots back. Each
limit follows from the memory and processors of the machine; an environment
variable replaces it.

    AIPROVER_MAX_HOSTED     concurrent calls to hosted models
    AIPROVER_MAX_TOOL_CALLS concurrent hosted calls with Lean tools
    AIPROVER_MAX_LEAN       concurrent Lean processes
    AIPROVER_MAX_SESSIONS   concurrent AIProver sessions
    AIPROVER_LEAN_MB        memory limit of one Lean process
    AIPROVER_LIMITS_DIR     directory of the lock files
"""

import fcntl
import math
import os
import random
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Peak memory of a Lean process that imports Mathlib, in GiB.
LEAN_GIB = 5
# Memory of a session, whose Lean server is shared with its tools, in GiB.
SESSION_GIB = 2
# Share of the memory Lean processes may use together; the remainder serves
# the model calls and the rest of the machine.
LEAN_MEMORY_SHARE = 0.75
MAX_HOSTED_CALLS = 32
TOOL_CALLS_PER_LEAN = 2
MAX_LEAN_MB = 8192
# Bounds of a `lean-check` command: its wait for a slot, its elaboration, and
# the whole command, which leaves it the time to report either.
LEAN_CHECK_SLOT_SECONDS = 120
LEAN_CHECK_WALL_SECONDS = 600
LEAN_CHECK_TIMEOUT_MS = 1000 * (
    LEAN_CHECK_SLOT_SECONDS + LEAN_CHECK_WALL_SECONDS + 30
)
# Seconds between attempts to take a slot, randomized to spread the waiters.
SLOT_POLL_SECONDS = (0.5, 2.0)


def memory_gib() -> float:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def _lean_processes() -> float:
    by_memory = memory_gib() * LEAN_MEMORY_SHARE / LEAN_GIB
    return min(os.cpu_count() or 1, by_memory)


def _setting(variable: str, derived: float) -> int:
    return max(1, int(os.environ.get(variable) or derived))


def lean_megabytes() -> int:
    return _setting("AIPROVER_LEAN_MB", min(MAX_LEAN_MB, memory_gib() * 512))


def _directory() -> Path:
    default = Path(tempfile.gettempdir()) / "aiprover_limits"
    directory = Path(os.environ.get("AIPROVER_LIMITS_DIR") or default)
    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
        # Open to every user; the sticky bit keeps each user's files.
        directory.chmod(0o1777)
    return directory


def _open_slot(path: Path) -> int:
    """The slot's file, read-only: `flock` needs no more, and a file of
    another user in a sticky directory may not be opened to create it."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return os.open(path, os.O_RDONLY)
    os.fchmod(descriptor, 0o644)
    return descriptor


def _take(resource: str, limit: int, wanted: int) -> list[int]:
    """Up to `wanted` slots that are free now, as open file descriptors."""
    directory, taken = _directory(), []
    for index in range(limit):
        if len(taken) == wanted:
            break
        descriptor = _open_slot(directory / f"{resource}{index}.lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            taken.append(descriptor)
        except BlockingIOError:
            os.close(descriptor)
    return taken


class HeldSlots:
    """Slots held for one piece of work, whose size may change: `take`
    never waits, so that a holder cannot block another."""

    def __init__(self, resource: str, limit: int):
        self.resource, self.limit = resource, limit
        self._lock = threading.Lock()
        self._descriptors: list[int] = []
        self._closed = False

    def __len__(self) -> int:
        return len(self._descriptors)

    def take(self, wanted: int) -> int:
        """Take up to `wanted` slots that are free; how many were taken."""
        with self._lock:
            if self._closed:
                return 0
            taken = _take(self.resource, self.limit, wanted)
            self._descriptors += taken
            return len(taken)

    def release(self, count: int) -> None:
        with self._lock:
            for _ in range(min(count, len(self._descriptors))):
                os.close(self._descriptors.pop())

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.release(len(self))


# One thread of a process polls each resource; its other waiters queue here.
_poll_gates: dict[str, threading.Lock] = {}


@contextmanager
def machine_slots(
    resource: str,
    limit: int,
    wanted: int,
    *stops,
    wait: float | None = None,
):
    """Hold up to `wanted` of the machine's `limit` slots of `resource`, as
    a HeldSlots. It waits for the first slot only: a waiter holds nothing,
    so waiters cannot block one another. The result is empty once one of
    the events `stops` (None for no event) or `wait` seconds end the wait."""
    held = HeldSlots(resource, limit)
    gate = _poll_gates.setdefault(resource, threading.Lock())
    deadline = math.inf if wait is None else time.monotonic() + wait
    try:
        while (
            not held
            and not any(event and event.is_set() for event in stops)
            and time.monotonic() < deadline
        ):
            pause = random.uniform(*SLOT_POLL_SECONDS)
            if gate.acquire(timeout=pause):
                try:
                    if not held.take(wanted):
                        time.sleep(pause)
                finally:
                    gate.release()
        yield held
    finally:
        held.close()


@dataclass(frozen=True)
class Resource:
    """A resource of the machine, whose limit follows from `derive` unless
    the environment `variable` replaces it."""

    name: str
    variable: str
    derive: Callable[[], float]

    def limit(self) -> int:
        return _setting(self.variable, self.derive())

    def slots(self, wanted: int = 1, *stops, wait: float | None = None):
        return machine_slots(
            self.name, self.limit(), wanted, *stops, wait=wait
        )


HOSTED = Resource(
    "hosted",
    "AIPROVER_MAX_HOSTED",
    lambda: min(MAX_HOSTED_CALLS, memory_gib()),
)
LEAN = Resource("lean", "AIPROVER_MAX_LEAN", _lean_processes)
# Calls with Lean tools, which beyond this count would mostly wait for Lean.
TOOL_CALLS = Resource(
    "tool_calls",
    "AIPROVER_MAX_TOOL_CALLS",
    lambda: TOOL_CALLS_PER_LEAN * LEAN.limit(),
)
SESSIONS = Resource(
    "sessions", "AIPROVER_MAX_SESSIONS", lambda: memory_gib() / SESSION_GIB
)
