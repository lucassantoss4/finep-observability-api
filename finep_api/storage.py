import sqlite3
from contextlib import contextmanager
from pathlib import Path

from opentelemetry import trace

from finep_api.domain import Opportunity

tracer = trace.get_tracer(__name__)


class Storage:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY, attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL, duration REAL NOT NULL,
                    records INTEGER NOT NULL, error_type TEXT);
            """)

    def snapshot(self):
        with tracer.start_as_current_span("db.snapshot"), self.connect() as db:
            rows = db.execute("SELECT payload FROM opportunities ORDER BY id").fetchall()
            last = db.execute("""SELECT attempted_at FROM collection_runs
                WHERE success = 1 ORDER BY id DESC LIMIT 1""").fetchone()
            return [Opportunity.model_validate_json(row[0]) for row in rows], last[0] if last else None

    def save_success(self, items: list[Opportunity], attempted_at: str, duration: float):
        with tracer.start_as_current_span("db.save_collection"), self.connect() as db:
            db.execute("DELETE FROM opportunities")
            db.executemany(
                "INSERT INTO opportunities VALUES (?, ?)",
                [(item.id, item.model_dump_json()) for item in items],
            )
            self._record(db, attempted_at, True, duration, len(items), None)

    def save_failure(self, attempted_at: str, duration: float, error: str):
        with self.connect() as db:
            self._record(db, attempted_at, False, duration, 0, error)

    @staticmethod
    def _record(db, attempted_at, success, duration, records, error):
        db.execute(
            """INSERT INTO collection_runs
            (attempted_at, success, duration, records, error_type) VALUES (?, ?, ?, ?, ?)""",
            (attempted_at, int(success), duration, records, error),
        )

    def collection_stats(self):
        with self.connect() as db:
            rows = db.execute("""SELECT success, COUNT(*) FROM collection_runs
                GROUP BY success""").fetchall()
            last = db.execute("""SELECT attempted_at, success, duration, records, error_type
                FROM collection_runs ORDER BY id DESC LIMIT 1""").fetchone()
            return {str(row[0]): row[1] for row in rows}, dict(last) if last else None

    def ready(self):
        with self.connect() as db:
            db.execute("SELECT COUNT(*) FROM opportunities").fetchone()
