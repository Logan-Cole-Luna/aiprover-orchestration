"""A SQLite database shared between threads, as used by the query server's
job store and the theorem library."""

import json
import sqlite3
import threading
from pathlib import Path


class SqliteStore:
    """One connection, serialized by a lock. Values of `json_columns` are
    stored as JSON text and returned decoded."""

    json_columns: tuple[str, ...] = ()

    def __init__(self, path: Path, schema: str, migrations: list[str] = ()):
        """Open the database at `path` and create its `schema`; each of
        `migrations` adds a column to a database created before it."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._connection.executescript(schema)
            for statement in migrations:
                try:
                    self._connection.execute(statement)
                except sqlite3.OperationalError:
                    pass  # the column exists
            self._connection.commit()

    def _encoded(self, values: dict) -> dict:
        return {
            key: json.dumps(value) if key in self.json_columns else value
            for key, value in values.items()
        }

    def _decoded(self, row: sqlite3.Row) -> dict:
        record = dict(row)
        for column in self.json_columns:
            if column in record:
                record[column] = json.loads(record[column])
        return record

    def _run(
        self, query: str, parameters: tuple = ()
    ) -> tuple[list[sqlite3.Row], int, int]:
        """(rows, rows changed, last row id) of one committed statement."""
        with self._lock:
            cursor = self._connection.execute(query, parameters)
            rows = cursor.fetchall()
            self._connection.commit()
        return rows, cursor.rowcount, cursor.lastrowid

    def _execute(self, query: str, parameters: tuple = ()) -> list[dict]:
        """The rows a query returns."""
        rows, _, _ = self._run(query, parameters)
        return [self._decoded(row) for row in rows]

    def _insert(self, table: str, record: dict) -> int:
        """Insert `record`; returns its row id."""
        record = self._encoded(record)
        columns = ", ".join(record)
        placeholders = ", ".join("?" for _ in record)
        _, _, row_id = self._run(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(record.values()),
        )
        return row_id

    def _update(self, table: str, where: dict, values: dict) -> int:
        """Set `values` in the rows matching every column of `where`;
        returns the number of rows changed."""
        values = self._encoded(values)
        assignments = ", ".join(f"{column} = ?" for column in values)
        condition = " AND ".join(f"{column} = ?" for column in where)
        _, changed, _ = self._run(
            f"UPDATE {table} SET {assignments} WHERE {condition}",
            (*values.values(), *where.values()),
        )
        return changed
