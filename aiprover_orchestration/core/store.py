"""SQLite records of library items, proof submissions and decompositions.

An item is a definition or a theorem. A submission is an attempted proof or
disproof of a theorem; a sketch is a submission that imports other theorems,
which become its children (`edges`). A sketch proves its parent once every
child is proved.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .layout import DATABASE

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- definition | theorem
    name TEXT NOT NULL UNIQUE,
    module TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    statement_nl TEXT NOT NULL DEFAULT '',
    lean TEXT NOT NULL,
    status TEXT NOT NULL,            -- Definition | Open | Proved | Disproved
    source TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theorem_id INTEGER NOT NULL REFERENCES items(id),
    proof_type TEXT NOT NULL,        -- prove | disprove
    content TEXT NOT NULL,
    status TEXT NOT NULL,            -- PENDING | ACCEPTED | SKETCH_ACCEPTED
                                     -- | REJECTED | ERROR
    error TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    axioms TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    child_id INTEGER NOT NULL REFERENCES items(id),
    PRIMARY KEY (submission_id, child_id)
);
"""
JSON_COLUMNS = ("tags", "axioms")
SKETCH_STATES = ("SKETCH_ACCEPTED", "ACCEPTED")


def _decode(row: sqlite3.Row) -> dict:
    record = dict(row)
    for column in JSON_COLUMNS:
        if column in record:
            record[column] = json.loads(record[column])
    return record


def implied_proofs(proved: set[int], sketches: list[dict]) -> set[int]:
    """Theorems proved by `proved` together with the given sketches.

    Each sketch is {"parent": id, "children": set of ids}; the result is the
    least fixed point of adding a sketch's parent once its children are in.
    """
    closure = set(proved)
    changed = True
    while changed:
        changed = False
        for sketch in sketches:
            if (sketch["parent"] not in closure
                    and sketch["children"] <= closure):
                closure.add(sketch["parent"])
                changed = True
    return closure


class Store:
    """Thread-safe access to one library database."""

    def __init__(self, path: Path = DATABASE):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._connection.executescript(SCHEMA)

    def _execute(self, query: str, parameters: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
            self._connection.commit()
        return [_decode(row) for row in rows]

    def _insert(self, table: str, record: dict) -> int:
        record = {key: json.dumps(value) if key in JSON_COLUMNS else value
                  for key, value in record.items()}
        columns = ", ".join(record)
        placeholders = ", ".join("?" for _ in record)
        with self._lock:
            cursor = self._connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                tuple(record.values()))
            self._connection.commit()
        return cursor.lastrowid

    # Items ---------------------------------------------------------------

    def add_item(self, **fields) -> int:
        return self._insert("items", {**fields, "created": time.time()})

    def item(self, value: int | str, by: str = "id") -> dict | None:
        """Item whose column `by` (id, name or module) equals `value`."""
        if by not in ("id", "name", "module"):
            raise ValueError(f"unknown item key {by!r}")
        rows = self._execute(f"SELECT * FROM items WHERE {by} = ?", (value,))
        return rows[0] if rows else None

    def items(self, ids: set[int]) -> list[dict]:
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        return self._execute(f"SELECT * FROM items WHERE id IN ({marks})",
                             tuple(ids))

    def search(self, query: str = "", status: str = "",
               kind: str = "") -> list[dict]:
        pattern = f"%{query}%"
        return self._execute(
            "SELECT * FROM items WHERE (name LIKE ? OR title LIKE ?"
            " OR statement_nl LIKE ?) AND (? = '' OR status = ?)"
            " AND (? = '' OR kind = ?) ORDER BY id",
            (pattern, pattern, pattern, status, status, kind, kind))

    def set_status(self, item_id: int, status: str) -> None:
        self._execute("UPDATE items SET status = ? WHERE id = ?",
                      (status, item_id))

    def proved(self) -> set[int]:
        rows = self._execute("SELECT id FROM items WHERE status = 'Proved'")
        return {row["id"] for row in rows}

    # Submissions ---------------------------------------------------------

    def add_submission(self, **fields) -> int:
        return self._insert("submissions", {**fields, "created": time.time()})

    def submission(self, submission_id: int) -> dict | None:
        rows = self._execute("SELECT * FROM submissions WHERE id = ?",
                             (submission_id,))
        return rows[0] if rows else None

    def submissions(self, theorem_id: int) -> list[dict]:
        return self._execute("SELECT * FROM submissions WHERE theorem_id = ?"
                             " ORDER BY id", (theorem_id,))

    def update_submission(self, submission_id: int, **values) -> None:
        values = {key: json.dumps(value) if key in JSON_COLUMNS else value
                  for key, value in values.items()}
        assignments = ", ".join(f"{column} = ?" for column in values)
        self._execute(f"UPDATE submissions SET {assignments} WHERE id = ?",
                      (*values.values(), submission_id))

    def add_edges(self, submission_id: int, child_ids: set[int]) -> None:
        for child_id in child_ids:
            self._insert("edges", {"submission_id": submission_id,
                                   "child_id": child_id})

    # Decomposition -------------------------------------------------------

    def sketches(self, parent_id: int | None = None) -> list[dict]:
        """Accepted sketches: {"id", "parent", "status", "children"}."""
        marks = ", ".join("?" for _ in SKETCH_STATES)
        rows = self._execute(
            "SELECT s.id, s.theorem_id, s.status, e.child_id"
            " FROM submissions s JOIN edges e ON e.submission_id = s.id"
            f" WHERE s.status IN ({marks}) AND (? IS NULL OR s.theorem_id = ?)"
            " ORDER BY s.id", (*SKETCH_STATES, parent_id, parent_id))
        sketches: dict[int, dict] = {}
        for row in rows:
            sketch = sketches.setdefault(row["id"], {
                "id": row["id"], "parent": row["theorem_id"],
                "status": row["status"], "children": set()})
            sketch["children"].add(row["child_id"])
        return list(sketches.values())

    def graph(self, theorem_id: int, _seen: frozenset = frozenset()) -> dict:
        """Decomposition tree below `theorem_id`."""
        item = self.item(theorem_id)
        node = {key: item[key] for key in ("id", "name", "title", "status")}
        if theorem_id in _seen:
            return {**node, "cycle": True}
        seen = _seen | {theorem_id}
        node["decompositions"] = [
            {"submission_id": sketch["id"], "status": sketch["status"],
             "children": [self.graph(child, seen)
                          for child in sorted(sketch["children"])]}
            for sketch in self.sketches(theorem_id)]
        return node

    def open_leaves(self, theorem_id: int) -> list[dict]:
        """Open undecomposed theorems below `theorem_id`.

        Each leaf carries `closability`: the number of further theorems
        that a proof of the leaf would resolve.
        """
        sketches = self.sketches()
        proved = implied_proofs(self.proved(), sketches)
        children = {}
        for sketch in sketches:
            children.setdefault(sketch["parent"], set()).update(
                sketch["children"])
        reachable, frontier = set(), [theorem_id]
        while frontier:
            node = frontier.pop()
            if node not in reachable:
                reachable.add(node)
                frontier.extend(children.get(node, ()))
        leaf_ids = {node for node in reachable
                    if node not in children and node not in proved}
        leaves = [
            {"theorem_id": leaf["id"], "name": leaf["name"],
             "status": leaf["status"],
             "closability": len(implied_proofs(proved | {leaf["id"]},
                                               sketches) - proved) - 1}
            for leaf in self.items(leaf_ids) if leaf["status"] == "Open"]
        return sorted(leaves, key=lambda leaf: -leaf["closability"])
