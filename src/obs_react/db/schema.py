"""Database connection, initialization, and migration."""

import sqlite3
from pathlib import Path

_SCHEMA_SQL = Path(__file__).parent / "schema.sql"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with row_factory set."""
    if db_path is None:
        from obs_react.config import DB_PATH
        db_path = DB_PATH
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Create tables from schema.sql and run migrations."""
    conn = get_connection(db_path)
    schema = _SCHEMA_SQL.read_text()
    conn.executescript(schema)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Run ALTER TABLE ADD COLUMN migrations for schema evolution."""
    # Future migrations go here. Pattern:
    # try:
    #     conn.execute("ALTER TABLE x ADD COLUMN y TEXT")
    # except sqlite3.OperationalError:
    #     pass  # column already exists
    pass
