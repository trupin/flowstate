"""Database connection management for Flowstate.

Provides FlowstateDB, which opens/creates a SQLite database, enables WAL mode,
sets pragmas, and initializes the schema on first connection.
"""

import sqlite3
from pathlib import Path


class FlowstateDB:
    """SQLite database wrapper for Flowstate.

    Opens or creates the database at the given path, configures WAL mode and
    other pragmas, and ensures the schema is initialized. Not thread-safe by
    design -- the execution engine ensures single-writer access.
    """

    def __init__(self, db_path: str = "~/.flowstate/flowstate.db") -> None:
        """Open or create the database and initialize the schema.

        Args:
            db_path: Path to the SQLite database file. Use ":memory:" for
                in-memory databases (tests). Defaults to ~/.flowstate/flowstate.db.
        """
        if db_path != ":memory:":
            resolved = Path(db_path).expanduser()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(resolved))
        else:
            self._conn = sqlite3.connect(":memory:")

        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self._initialize_schema()

    def _configure_pragmas(self) -> None:
        """Enable WAL mode, busy timeout, foreign keys, and journal size limit."""
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_size_limit=67108864")

    def _initialize_schema(self) -> None:
        """Read and execute schema.sql to create tables and indexes.

        Uses IF NOT EXISTS for idempotency -- safe to call multiple times.

        Raises:
            FileNotFoundError: If schema.sql is missing from the package.
        """
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(
                f"Schema file not found: {schema_path}. "
                "The flowstate package may be installed incorrectly."
            )
        schema_sql = schema_path.read_text()
        self._conn.executescript(schema_sql)
        self._migrate()

    def _migrate(self) -> None:
        """Apply incremental migrations for existing databases.

        Uses PRAGMA user_version to track which migrations have been applied.
        Each migration bumps the version by one.
        """
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]

        if version < 1:
            # Migration 1: Add 'pausing' to flow_runs.status CHECK constraint.
            # SQLite doesn't support ALTER CHECK, so we recreate the table.
            self._conn.executescript("""
                PRAGMA foreign_keys=OFF;
                BEGIN;

                CREATE TABLE IF NOT EXISTS flow_runs_new (
                    id TEXT PRIMARY KEY,
                    flow_definition_id TEXT NOT NULL REFERENCES flow_definitions(id),
                    status TEXT NOT NULL CHECK(status IN (
                        'created', 'running', 'pausing', 'paused', 'completed',
                        'failed', 'cancelled', 'budget_exceeded'
                    )),
                    default_workspace TEXT,
                    data_dir TEXT NOT NULL,
                    params_json TEXT,
                    budget_seconds INTEGER NOT NULL,
                    elapsed_seconds REAL DEFAULT 0,
                    on_error TEXT NOT NULL CHECK(on_error IN ('pause', 'abort', 'skip')),
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    error_message TEXT,
                    worktree_path TEXT,
                    task_id TEXT REFERENCES tasks(id)
                );

                INSERT OR IGNORE INTO flow_runs_new
                    SELECT * FROM flow_runs;

                DROP TABLE flow_runs;
                ALTER TABLE flow_runs_new RENAME TO flow_runs;

                CREATE INDEX IF NOT EXISTS idx_flow_runs_status ON flow_runs(status);

                COMMIT;
                PRAGMA foreign_keys=ON;
                PRAGMA user_version=1;
            """)

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the underlying sqlite3 connection."""
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "FlowstateDB":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()
