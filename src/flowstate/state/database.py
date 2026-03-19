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
