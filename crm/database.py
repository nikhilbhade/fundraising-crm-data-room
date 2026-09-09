"""Portable persistence, seeding, auditing, and CSV import/export.

SQLite remains the zero-configuration local default. When PostgreSQL
environment variables are present, the same application uses a pooled Cloud
SQL connection instead. Every user mutation is paired with an append-only
change-log row in the same transaction.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

import pandas as pd
from sqlalchemy import URL, create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine

from . import constants as C

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"
SEED_DIR = PROJECT_DIR / "seed"
SEED_REVISION = 2
AUDIT_TABLE = "change_log"
_ACTOR: ContextVar[str] = ContextVar("fundraising_crm_actor", default="")

# Load order matters: parents before children.
SEED_ORDER = [
    "firms",
    "fund_vehicles",
    "contacts",
    "thesis_tags",
    "firm_tags",
    "portfolio_companies",
    "firm_portfolio",
    "competitors",
    "conflict_checks",
    "intro_paths",
    "rounds",
    "opportunities",
    "activities",
    "objections",
    "diligence_requests",
    "tasks",
    "documents",
    "document_shares",
    "sources",
    "investor_signals",
    "firm_signals",
    "news_items",
    "programs",
    "program_cycles",
]

PRIMARY_KEYS = {
    "firms": ["firm_id"],
    "fund_vehicles": ["fund_id"],
    "contacts": ["contact_id"],
    "thesis_tags": ["tag_id"],
    "firm_tags": ["firm_id", "tag_id"],
    "portfolio_companies": ["company_id"],
    "firm_portfolio": ["firm_id", "company_id"],
    "competitors": ["competitor_id"],
    "conflict_checks": ["check_id"],
    "intro_paths": ["intro_id"],
    "rounds": ["round_id"],
    "opportunities": ["opportunity_id"],
    "activities": ["activity_id"],
    "objections": ["objection_id"],
    "diligence_requests": ["request_id"],
    "tasks": ["task_id"],
    "documents": ["document_id"],
    "document_shares": ["share_id"],
    "sources": ["source_id"],
    "investor_signals": ["signal_id"],
    "firm_signals": ["firm_id", "signal_id"],
    "news_items": ["news_id"],
    "programs": ["program_id"],
    "program_cycles": ["cycle_id"],
    "scoring_weights": ["key"],
}

# CREATE TABLE IF NOT EXISTS does not add columns to an existing user's local
# database. These additive migrations keep old clones compatible with releases
# that extend the CRM while preserving every private note and pipeline edit.
ADDITIVE_MIGRATIONS = {
    "firms": {
        "access_mode": "TEXT DEFAULT 'Research needed'",
        "application_url": "TEXT",
        "access_notes": "TEXT",
        "decision_process": "TEXT",
        "decision_makers": "TEXT",
        "decision_timeline_days": "INTEGER",
        "decision_notes": "TEXT",
        "target_partner_role": "TEXT",
        "linkedin_query": "TEXT",
    },
    "opportunities": {
        "application_status": "TEXT DEFAULT 'Not applicable'",
        "application_started_on": "TEXT",
        "application_submitted_on": "TEXT",
        "application_deadline": "TEXT",
        "decision_status": "TEXT DEFAULT 'Unknown'",
        "decision_next_gate": "TEXT",
        "decision_expected": "TEXT",
    },
    "tasks": {
        "workstream": "TEXT DEFAULT 'Track'",
        "board_status": "TEXT DEFAULT 'Backlog'",
        "task_type": "TEXT DEFAULT 'Task'",
        "description": "TEXT",
        "acceptance_criteria": "TEXT",
        "blocked_by": "TEXT",
        "sort_order": "INTEGER DEFAULT 100",
    },
}


class Record:
    """Small row object compatible with both sqlite-style indexes and keys."""

    def __init__(self, keys: Sequence[str], values: Sequence[Any]):
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)

    def keys(self):
        return self._mapping.keys()

    def items(self):
        return self._mapping.items()

    def values(self):
        return self._mapping.values()


class BufferedResult:
    """A detached result whose rows remain usable after a pooled connection closes."""

    def __init__(self, rows: List[Record], rowcount: int = 0, keys: Sequence[str] = ()):
        self._rows = rows
        self._position = 0
        self.rowcount = rowcount
        self.keys = tuple(keys)

    def fetchone(self) -> Optional[Record]:
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchall(self) -> List[Record]:
        rows = self._rows[self._position :]
        self._position = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Record]:
        return iter(self.fetchall())


def _statement(sql: str, params: Iterable[Any] | Mapping[str, Any] = ()):
    """Turn the app's compact positional SQL into portable named binds."""
    if isinstance(params, Mapping):
        return text(sql), dict(params)
    values = tuple(params or ())
    if not values:
        return text(sql), {}
    pieces = sql.split("?")
    if len(pieces) - 1 != len(values):
        raise ValueError("SQL placeholder count does not match supplied values")
    rebuilt = pieces[0]
    bindings: Dict[str, Any] = {}
    for index, value in enumerate(values):
        key = f"p{index}"
        rebuilt += f":{key}{pieces[index + 1]}"
        bindings[key] = value
    return text(rebuilt), bindings


def _execute_on(
    connection: Connection,
    sql: str,
    params: Iterable[Any] | Mapping[str, Any] = (),
) -> BufferedResult:
    statement, bindings = _statement(sql, params)
    result = connection.execute(statement, bindings)
    if not result.returns_rows:
        return BufferedResult([], result.rowcount or 0)
    keys = list(result.keys())
    return BufferedResult(
        [Record(keys, tuple(row)) for row in result.fetchall()], result.rowcount or 0, keys
    )


class Database:
    """Thread-safe engine facade used by Streamlit and the test suite."""

    def __init__(self, engine: Engine, backend: str, location: str):
        self.engine = engine
        self.backend = backend
        self.location = location
        self._column_cache: Dict[str, List[str]] = {}

    def execute(
        self, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
    ) -> BufferedResult:
        with self.engine.begin() as transaction:
            return _execute_on(transaction, sql, params)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> int:
        row_list = [tuple(row) for row in rows]
        if not row_list:
            return 0
        statement, _ = _statement(sql, row_list[0])
        bindings = [_statement(sql, row)[1] for row in row_list]
        with self.engine.begin() as transaction:
            result = transaction.execute(statement, bindings)
        return result.rowcount or len(row_list)

    def executescript(self, script: str) -> None:
        portable = "\n".join(
            line for line in script.splitlines() if not line.lstrip().upper().startswith("PRAGMA ")
        )
        raw = self.engine.raw_connection()
        try:
            cursor = raw.cursor()
            if self.backend == "sqlite":
                cursor.executescript(portable)
            else:
                cursor.execute(portable, prepare=False)
            raw.commit()
            cursor.close()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def begin(self):
        return self.engine.begin()

    def commit(self) -> None:
        # Public execute calls already commit atomically through engine.begin().
        return None

    def close(self) -> None:
        self.engine.dispose()


def db_path() -> Path:
    """Where the working database lives. Override with FUNDRAISING_CRM_DB."""
    return Path(os.environ.get("FUNDRAISING_CRM_DB", PROJECT_DIR / C.DB_FILENAME))


def _postgres_requested() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")) or (
        os.environ.get("DB_ENGINE", "").lower() in {"postgres", "postgresql"}
    )


def connect() -> Database:
    if _postgres_requested():
        configured_url = os.environ.get("DATABASE_URL")
        connect_args: Dict[str, Any] = {}
        if configured_url:
            if configured_url.startswith("postgres://"):
                configured_url = configured_url.replace(
                    "postgres://", "postgresql+psycopg://", 1
                )
            elif configured_url.startswith("postgresql://"):
                configured_url = configured_url.replace(
                    "postgresql://", "postgresql+psycopg://", 1
                )
            url: Any = configured_url
            location = "PostgreSQL"
        else:
            socket = os.environ.get("INSTANCE_UNIX_SOCKET")
            if not socket and os.environ.get("INSTANCE_CONNECTION_NAME"):
                socket = f"/cloudsql/{os.environ['INSTANCE_CONNECTION_NAME']}"
            host = socket or os.environ.get("DB_HOST", "127.0.0.1")
            if socket:
                connect_args["host"] = socket
                connect_args["port"] = int(os.environ.get("DB_PORT", "5432"))
            url = URL.create(
                "postgresql+psycopg",
                username=os.environ.get("DB_USER", "fundraising_app"),
                password=os.environ.get("DB_PASSWORD", ""),
                host=None if socket else host,
                port=None if socket else int(os.environ.get("DB_PORT", "5432")),
                database=os.environ.get("DB_NAME", "fundraising_crm"),
            )
            location = f"Cloud SQL · {os.environ.get('DB_NAME', 'fundraising_crm')}"
        engine = create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=int(os.environ.get("DB_POOL_SIZE", "3")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "1")),
        )
        return Database(engine, "postgresql", location)

    path = db_path().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    return Database(engine, "sqlite", str(path))


def set_actor(actor: Optional[str]) -> None:
    _ACTOR.set((actor or "").strip())


def current_actor() -> str:
    return _ACTOR.get() or os.environ.get("CRM_DEFAULT_ACTOR", "Local owner")


def backend_label(conn: Database) -> str:
    return conn.location


def is_cloud_database(conn: Database) -> bool:
    return conn.backend == "postgresql"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def today_iso() -> str:
    return _dt.date.today().isoformat()


# ------------------------------------------------------------ introspection --
def table_columns(conn: Database, table: str) -> List[str]:
    """Return table columns without repeating remote schema lookups per row."""
    if table not in conn._column_cache:
        conn._column_cache[table] = [
            column["name"] for column in inspect(conn.engine).get_columns(table)
        ]
    return list(conn._column_cache[table])


def table_exists(conn: Database, table: str) -> bool:
    return inspect(conn.engine).has_table(table)


# ------------------------------------------------------------------ schema --
def init_schema(conn: Database) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    # A prior inspection may have occurred while the database was only partly
    # initialized. Refresh once here; normal reads and imports can then reuse
    # the stable metadata instead of making a network round-trip per row.
    conn._column_cache.clear()
    apply_additive_migrations(conn)
    conn.commit()


def apply_additive_migrations(conn: Database) -> None:
    """Apply safe column-only migrations to databases created by older builds."""
    for table, columns in ADDITIVE_MIGRATIONS.items():
        existing = set(table_columns(conn, table))
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                conn._column_cache.pop(table, None)
                existing.add(name)
    conn.execute(
        "UPDATE tasks SET board_status = 'Done' "
        "WHERE status = 'Done' AND (board_status IS NULL OR board_status != 'Done')"
    )
    conn.execute(
        "UPDATE tasks SET board_status = 'Ready' "
        "WHERE status = 'Open' AND (board_status IS NULL OR board_status = '')"
    )


def is_empty(conn: Database) -> bool:
    if not table_exists(conn, "firms"):
        return True
    return conn.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"] == 0


# -------------------------------------------------------------------- seed --
def _coerce(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """Keep only columns the table actually has, and turn NaN into None."""
    keep = [c for c in df.columns if c in columns]
    out = df[keep].copy()
    return out.where(pd.notna(out), None)


def _python_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _record_dict(row: Optional[Record]) -> Optional[Dict[str, Any]]:
    return None if row is None else {key: _python_value(row[key]) for key in row.keys()}


def _where(pk_values: Mapping[str, Any]) -> str:
    return " AND ".join(f"{key} = ?" for key in pk_values)


def _fetch_record_on(
    transaction: Connection, table: str, pk_values: Mapping[str, Any]
) -> Optional[Dict[str, Any]]:
    row = _execute_on(
        transaction,
        f"SELECT * FROM {table} WHERE {_where(pk_values)} LIMIT 1",
        tuple(pk_values.values()),
    ).fetchone()
    return _record_dict(row)


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _audit_on(
    transaction: Connection,
    *,
    action: str,
    table: str,
    pk_values: Mapping[str, Any],
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    source: str,
    batch_id: Optional[str] = None,
) -> None:
    if table in {AUDIT_TABLE, "app_metadata"}:
        return
    old = before or {}
    new = after or {}
    changed = sorted(
        key for key in set(old) | set(new) if _python_value(old.get(key)) != _python_value(new.get(key))
    )
    if before is not None and after is not None and not changed:
        return
    _execute_on(
        transaction,
        """INSERT INTO change_log
           (change_id, actor, action, table_name, record_key, changed_fields,
            before_json, after_json, source, batch_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id("chg"), current_actor(), action, table, _json(dict(pk_values)),
            _json(changed), _json(before), _json(after), source, batch_id,
        ),
    )


def _update_row_on(
    transaction: Connection,
    conn: Database,
    table: str,
    pk_values: Dict[str, Any],
    fields: Dict[str, Any],
    *,
    audit: bool = True,
    action: str = "UPDATE",
    source: str = "app",
    batch_id: Optional[str] = None,
) -> bool:
    before = _fetch_record_on(transaction, table, pk_values)
    if before is None:
        return False
    cols = table_columns(conn, table)
    clean = {key: _python_value(value) for key, value in fields.items() if key in cols}
    clean = {key: value for key, value in clean.items() if before.get(key) != value}
    if not clean:
        return False
    if "updated_at" in cols and "updated_at" not in clean:
        clean["updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    setters = ", ".join(f"{key} = ?" for key in clean)
    _execute_on(
        transaction,
        f"UPDATE {table} SET {setters} WHERE {_where(pk_values)}",
        tuple(clean.values()) + tuple(pk_values.values()),
    )
    after = _fetch_record_on(transaction, table, pk_values)
    if audit:
        _audit_on(
            transaction, action=action, table=table, pk_values=pk_values,
            before=before, after=after, source=source, batch_id=batch_id,
        )
    return True


def _insert_row_on(
    transaction: Connection,
    conn: Database,
    table: str,
    fields: Dict[str, Any],
    *,
    audit: bool = True,
    action: str = "INSERT",
    source: str = "app",
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    cols = table_columns(conn, table)
    clean = {key: _python_value(value) for key, value in fields.items() if key in cols}
    names = ", ".join(clean)
    placeholders = ", ".join("?" for _ in clean)
    _execute_on(
        transaction,
        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
        tuple(clean.values()),
    )
    pk = PRIMARY_KEYS.get(table, [])
    pk_values = {key: clean.get(key) for key in pk}
    after = _fetch_record_on(transaction, table, pk_values) if pk and all(
        value is not None for value in pk_values.values()
    ) else dict(clean)
    if audit:
        _audit_on(
            transaction, action=action, table=table, pk_values=pk_values,
            before=None, after=after, source=source, batch_id=batch_id,
        )
    return after or dict(clean)


def upsert_dataframe(
    conn: Database,
    table: str,
    df: pd.DataFrame,
    replace: bool = False,
    *,
    audit: bool = True,
    source: str = "csv_import",
) -> int:
    """Insert or update rows keyed on the table's primary key.

    ``replace=True`` overwrites existing rows entirely; otherwise only the
    columns present in ``df`` are written, so a partial CSV can patch a table
    without blanking the fields it omits.
    """
    if df is None or df.empty:
        return 0
    cols = table_columns(conn, table)
    data = _coerce(df, cols)
    if data.empty or not len(data.columns):
        return 0

    pk = PRIMARY_KEYS.get(table, [])
    missing_pk = [k for k in pk if k not in data.columns]
    if missing_pk:
        raise ValueError(
            f"{table}: CSV is missing primary key column(s) {', '.join(missing_pk)}"
        )

    batch_id = new_id("batch") if audit else None
    with conn.begin() as transaction:
        for raw in data.to_dict(orient="records"):
            row = {key: _python_value(value) for key, value in raw.items()}
            pk_values = {key: row[key] for key in pk}
            before = _fetch_record_on(transaction, table, pk_values)
            if before is None:
                _insert_row_on(
                    transaction, conn, table, row, audit=audit, action="IMPORT" if audit else "INSERT",
                    source=source, batch_id=batch_id,
                )
                continue
            fields = {key: value for key, value in row.items() if key not in pk}
            # Replacement is implemented as an UPDATE rather than SQLite's
            # delete-plus-insert behavior, so related CRM records cannot cascade away.
            _update_row_on(
                transaction, conn, table, pk_values, fields, audit=audit,
                action="IMPORT" if audit else "UPDATE", source=source, batch_id=batch_id,
            )
    return len(data)


def load_seed(conn: Database, tables: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """Load seed CSVs into the database. Safe to re-run: rows are upserted."""
    counts: Dict[str, int] = {}
    for table in tables or SEED_ORDER:
        path = SEED_DIR / f"{table}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        try:
            counts[table] = upsert_dataframe(conn, table, df, audit=False, source="seed")
        except Exception as exc:  # a bad seed row should not brick the app
            counts[table] = 0
            print(f"[seed] {table}: {exc}")
    seed_scoring_defaults(conn)
    return counts


def merge_seed_defaults(
    conn: Database,
    tables: Optional[Iterable[str]] = None,
    *,
    audit: bool = False,
    source: str = "seed_upgrade",
) -> Dict[str, int]:
    """Add public seed rows and fill blanks without replacing user edits."""
    counts: Dict[str, int] = {}
    for table in tables or SEED_ORDER:
        path = SEED_DIR / f"{table}.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        data = _coerce(frame, table_columns(conn, table))
        pk = PRIMARY_KEYS.get(table, [])
        names = list(data.columns)
        if data.empty or not pk or any(k not in names for k in pk):
            counts[table] = 0
            continue

        with conn.begin() as transaction:
            for raw in data.to_dict(orient="records"):
                row = {key: _python_value(value) for key, value in raw.items()}
                pk_values = {key: row[key] for key in pk}
                existing = _fetch_record_on(transaction, table, pk_values)
                if existing is None:
                    _insert_row_on(
                        transaction,
                        conn,
                        table,
                        row,
                        audit=audit,
                        action="IMPORT" if audit else "INSERT",
                        source=source,
                    )
                    continue
                fills = {
                    key: value
                    for key, value in row.items()
                    if key not in pk
                    and (existing.get(key) is None or (
                        isinstance(existing.get(key), str) and not existing.get(key).strip()
                    ))
                    and value not in (None, "")
                }
                if fills:
                    _update_row_on(
                        transaction, conn, table, pk_values, fills,
                        audit=audit,
                        action="IMPORT" if audit else "UPDATE",
                        source=source,
                    )
        counts[table] = len(data)
    seed_scoring_defaults(conn)
    conn.commit()
    return counts


def _seed_revision(conn: Database) -> int:
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key='seed_revision'"
    ).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _mark_seed_revision(conn: Database) -> None:
    conn.execute(
        "INSERT INTO app_metadata(key, value) VALUES('seed_revision', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SEED_REVISION),),
    )
    conn.commit()


def bootstrap() -> Database:
    """Open the database, creating and seeding it the first time."""
    conn = connect()
    init_schema(conn)
    if is_empty(conn):
        load_seed(conn)
        _mark_seed_revision(conn)
    elif _seed_revision(conn) < SEED_REVISION:
        merge_seed_defaults(conn)
        _mark_seed_revision(conn)
    else:
        seed_scoring_defaults(conn)
    return conn


def reset_database() -> None:
    if _postgres_requested():
        raise RuntimeError("Cloud databases cannot be deleted from the application")
    p = db_path()
    if p.exists():
        p.unlink()


# ----------------------------------------------------------------- scoring --
def seed_scoring_defaults(conn: Database) -> None:
    """Insert any scoring key that isn't set yet, leaving your edits alone."""
    rows: List[tuple] = []
    for k, v in C.DEFAULT_COMPONENT_WEIGHTS.items():
        rows.append((k, v, C.COMPONENT_LABELS.get(k, k), "component"))
    for k, v in C.DEFAULT_PENALTIES.items():
        rows.append((k, v, C.PENALTY_LABELS.get(k, k), "penalty"))
    for k, v in C.DEFAULT_GEO_SCORES.items():
        label = C.GEO_SEGMENT_LABELS.get(k.replace("geo_", ""), k)
        rows.append((k, v, f"Geography weight — {label}", "geo"))
    for k, v in C.DEFAULT_SETTINGS.items():
        rows.append((k, v, C.SETTING_LABELS.get(k, k), "setting"))
    conn.executemany(
        "INSERT INTO scoring_weights (key, value, label, kind) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET label=excluded.label, kind=excluded.kind",
        rows,
    )
    conn.commit()


def get_weights(conn: Database) -> Dict[str, float]:
    return {r["key"]: float(r["value"]) for r in conn.execute("SELECT key, value FROM scoring_weights")}


def set_weight(conn: Database, key: str, value: float, *, source: str = "scoring") -> None:
    with conn.begin() as transaction:
        existing = _fetch_record_on(transaction, "scoring_weights", {"key": key})
        if existing is None:
            _insert_row_on(
                transaction, conn, "scoring_weights", {"key": key, "value": float(value)},
                source=source,
            )
        else:
            _update_row_on(
                transaction, conn, "scoring_weights", {"key": key},
                {"value": float(value)}, source=source,
            )


def reset_weights(conn: Database) -> None:
    before = q(conn, "SELECT * FROM scoring_weights ORDER BY key").to_dict("records")
    with conn.begin() as transaction:
        _execute_on(transaction, "DELETE FROM scoring_weights")
        rows: List[tuple] = []
        for key, value in C.DEFAULT_COMPONENT_WEIGHTS.items():
            rows.append((key, value, C.COMPONENT_LABELS.get(key, key), "component"))
        for key, value in C.DEFAULT_PENALTIES.items():
            rows.append((key, value, C.PENALTY_LABELS.get(key, key), "penalty"))
        for key, value in C.DEFAULT_GEO_SCORES.items():
            label = C.GEO_SEGMENT_LABELS.get(key.replace("geo_", ""), key)
            rows.append((key, value, f"Geography weight — {label}", "geo"))
        for key, value in C.DEFAULT_SETTINGS.items():
            rows.append((key, value, C.SETTING_LABELS.get(key, key), "setting"))
        insert_sql = (
            "INSERT INTO scoring_weights (key, value, label, kind) VALUES (?, ?, ?, ?)"
        )
        statement, _ = _statement(insert_sql, rows[0])
        transaction.execute(statement, [_statement(insert_sql, row)[1] for row in rows])
        after_rows = _execute_on(
            transaction, "SELECT * FROM scoring_weights ORDER BY key"
        ).fetchall()
        after = [{key: row[key] for key in row.keys()} for row in after_rows]
        _audit_on(
            transaction, action="RESET", table="scoring_weights", pk_values={"key": "*"},
            before={"weights": before}, after={"weights": after}, source="scoring_reset",
        )


def get_tag_weights(conn: Database) -> Dict[str, float]:
    return {
        r["tag_id"]: float(r["default_weight"] or 0)
        for r in conn.execute("SELECT tag_id, default_weight FROM thesis_tags")
    }


# ------------------------------------------------------------------ reads ---
def q(conn: Database, sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
    with conn.engine.connect() as connection:
        result = _execute_on(connection, sql, params)
    rows = result.fetchall()
    return pd.DataFrame(
        [{key: row[key] for key in row.keys()} for row in rows],
        columns=list(result.keys),
    )


def fetch_table(conn: Database, table: str) -> pd.DataFrame:
    return q(conn, f"SELECT * FROM {table}")


def active_round(conn: Database) -> Optional[Record]:
    row = conn.execute(
        "SELECT * FROM rounds WHERE is_active = 1 ORDER BY open_date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM rounds ORDER BY open_date, round_id LIMIT 1"
        ).fetchone()
    return row


# ----------------------------------------------------------------- writes ---
def update_row(
    conn: Database,
    table: str,
    pk_values: Dict[str, Any],
    fields: Dict[str, Any],
    *,
    source: str = "app",
) -> bool:
    if table == AUDIT_TABLE:
        raise ValueError("The changelog is append-only")
    if not fields:
        return False
    with conn.begin() as transaction:
        return _update_row_on(
            transaction, conn, table, pk_values, fields, source=source
        )


def insert_row(
    conn: Database,
    table: str,
    fields: Dict[str, Any],
    *,
    source: str = "app",
) -> Dict[str, Any]:
    if table == AUDIT_TABLE:
        raise ValueError("The changelog is append-only")
    with conn.begin() as transaction:
        return _insert_row_on(transaction, conn, table, fields, source=source)


def delete_row(
    conn: Database,
    table: str,
    pk_values: Dict[str, Any],
    *,
    source: str = "app",
) -> bool:
    if table == AUDIT_TABLE:
        raise ValueError("The changelog is append-only")
    with conn.begin() as transaction:
        before = _fetch_record_on(transaction, table, pk_values)
        if before is None:
            return False
        _execute_on(
            transaction, f"DELETE FROM {table} WHERE {_where(pk_values)}",
            tuple(pk_values.values()),
        )
        _audit_on(
            transaction, action="DELETE", table=table, pk_values=pk_values,
            before=before, after=None, source=source,
        )
    return True


# --------------------------------------------------------- conflict engine --
def recompute_conflicts(conn: Database) -> Dict[str, int]:
    """Re-derive every firm's engagement status from the competitor watchlist.

    Rule: a firm that has backed an active *Blocking* competitor is set to
    "Do Not Engage". A *Review*-severity competitor, or a portfolio row marked
    POTENTIAL_CONFLICT, sets "Hold - Review Conflict". Everything else is
    cleared to "Engage" — unless a human wrote an engagement_reason starting
    with "MANUAL:", which is always left alone.
    """
    with conn.begin() as transaction:
        blocking = {
            row["competitor_id"]
            for row in _execute_on(
                transaction,
                "SELECT competitor_id FROM competitors "
                "WHERE is_active = 1 AND severity = 'Blocking'",
            )
        }
        review = {
            row["competitor_id"]
            for row in _execute_on(
                transaction,
                "SELECT competitor_id FROM competitors "
                "WHERE is_active = 1 AND severity = 'Review'",
            )
        }
        comp_by_name = {
            (row["name"] or "").strip().lower(): row["competitor_id"]
            for row in _execute_on(
                transaction,
                "SELECT competitor_id, name FROM competitors WHERE is_active = 1",
            )
        }
        portfolio = _execute_on(
            transaction,
            """SELECT fp.firm_id, fp.relevance, pc.name AS company_name
               FROM firm_portfolio fp
               JOIN portfolio_companies pc ON pc.company_id = fp.company_id""",
        ).fetchall()

        verdicts: Dict[str, str] = {}
        reasons: Dict[str, str] = {}
        for row in portfolio:
            firm_id = row["firm_id"]
            relevance = (row["relevance"] or "").upper()
            competitor_id = comp_by_name.get((row["company_name"] or "").strip().lower())
            verdict = None
            if relevance == "DIRECT_CONFLICT" or (competitor_id and competitor_id in blocking):
                verdict = "Do Not Engage"
            elif relevance == "POTENTIAL_CONFLICT" or (competitor_id and competitor_id in review):
                verdict = "Hold - Review Conflict"
            if verdict is None:
                continue
            rank = {"Do Not Engage": 2, "Hold - Review Conflict": 1}
            if rank[verdict] > rank.get(verdicts.get(firm_id, ""), 0):
                verdicts[firm_id] = verdict
                reasons[firm_id] = (
                    f"Backed {row['company_name']} "
                    f"({relevance.replace('_', ' ').lower()})"
                )

        for row in _execute_on(
            transaction,
            "SELECT firm_id, resolution, notes FROM conflict_checks "
            "WHERE resolution IS NOT NULL",
        ):
            if row["resolution"] in C.ENGAGEMENT_STATUSES:
                verdicts[row["firm_id"]] = row["resolution"]
                reasons[row["firm_id"]] = row["notes"] or "Set by conflict check"

        counts = {"Engage": 0, "Hold - Review Conflict": 0, "Do Not Engage": 0}
        for row in _execute_on(
            transaction,
            "SELECT firm_id, engagement_status, engagement_reason FROM firms",
        ):
            firm_id = row["firm_id"]
            if (row["engagement_reason"] or "").startswith("MANUAL:"):
                existing = row["engagement_status"]
                counts[existing if existing in counts else "Engage"] += 1
                continue
            status = verdicts.get(firm_id, "Engage")
            reason = reasons.get(firm_id, "No conflict found against the active watchlist")
            _update_row_on(
                transaction,
                conn,
                "firms",
                {"firm_id": firm_id},
                {
                    "engagement_status": status,
                    "engagement_reason": reason,
                    "conflict_flag": 1 if status != "Engage" else 0,
                    "conflict_note": reason if status != "Engage" else None,
                },
                source="conflict_recompute",
            )
            counts[status] += 1
        return counts


# ---------------------------------------------------------- import/export ---
def export_table_csv(conn: Database, table: str) -> bytes:
    df = fetch_table(conn, table)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def export_all_csv(conn: Database) -> bytes:
    """Every table as one zip — the portable form of your whole CRM."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in SEED_ORDER + ["scoring_weights", AUDIT_TABLE]:
            if table_exists(conn, table):
                z.writestr(f"{table}.csv", export_table_csv(conn, table))
    return buf.getvalue()


def import_csv(
    conn: Database, table: str, file_like, replace: bool = False
) -> Dict[str, Any]:
    df = pd.read_csv(file_like, dtype=str, keep_default_na=False, na_values=[""])
    known = set(table_columns(conn, table))
    unknown = [c for c in df.columns if c not in known]
    n = upsert_dataframe(conn, table, df, replace=replace)
    return {"rows": n, "ignored_columns": unknown}
