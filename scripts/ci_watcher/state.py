"""SQLite-backed dedupe table for the CI watcher listener."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class State:
    """Tracks (run_id, attempt) pairs we've already enqueued."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_runs (
                    run_id INTEGER NOT NULL,
                    run_attempt INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    PRIMARY KEY (run_id, run_attempt)
                )
                """
            )

    def is_seen(self, *, run_id: int, attempt: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_runs WHERE run_id = ? AND run_attempt = ?",
                (run_id, attempt),
            ).fetchone()
            return row is not None

    def mark_seen(self, *, run_id: int, attempt: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_runs (run_id, run_attempt, received_at)
                VALUES (?, ?, ?)
                """,
                (run_id, attempt, int(time.time())),
            )
