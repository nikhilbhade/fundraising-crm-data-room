"""SQLite persistence, seeding and CSV import/export.

The database is created on first run from ``schema.sql`` and populated from
the CSVs in ``seed/``. Your working database is local and gitignored: the
seed files are the shared starting point, your ``fundraising.db`` is yours.
"""

from __future__ import annotations

import datetime as _dt
import io
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from . import constants as C

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"
SEED_DIR = PROJECT_DIR / "seed"

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
}


def db_path() -> Path:
    """Where the working database lives. Override with FUNDRAISING_CRM_DB."""
    return Path(os.environ.get("FUNDRAISING_CRM_DB", PROJECT_DIR / C.DB_FILENAME))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def today_iso() -> str:
    return _dt.date.today().isoformat()


# ------------------------------------------------------------ introspection --
def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ------------------------------------------------------------------ schema --
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def is_empty(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "firms"):
        return True
    return conn.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"] == 0


# -------------------------------------------------------------------- seed --
def _coerce(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """Keep only columns the table actually has, and turn NaN into None."""
    keep = [c for c in df.columns if c in columns]
    out = df[keep].copy()
    return out.where(pd.notna(out), None)


def upsert_dataframe(
    conn: sqlite3.Connection, table: str, df: pd.DataFrame, replace: bool = False
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

    names = list(data.columns)
    placeholders = ", ".join("?" for _ in names)
    col_list = ", ".join(names)

    if replace or not pk:
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    else:
        updatable = [c for c in names if c not in pk]
        if updatable:
            setters = ", ".join(f"{c}=excluded.{c}" for c in updatable)
            conflict = f"ON CONFLICT({', '.join(pk)}) DO UPDATE SET {setters}"
        else:
            conflict = f"ON CONFLICT({', '.join(pk)}) DO NOTHING"
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) {conflict}"

    rows = [tuple(r) for r in data.itertuples(index=False, name=None)]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def load_seed(conn: sqlite3.Connection, tables: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """Load seed CSVs into the database. Safe to re-run: rows are upserted."""
    counts: Dict[str, int] = {}
    for table in tables or SEED_ORDER:
        path = SEED_DIR / f"{table}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        try:
            counts[table] = upsert_dataframe(conn, table, df)
        except Exception as exc:  # a bad seed row should not brick the app
            counts[table] = 0
            print(f"[seed] {table}: {exc}")
    seed_scoring_defaults(conn)
    return counts


def bootstrap() -> sqlite3.Connection:
    """Open the database, creating and seeding it the first time."""
    conn = connect()
    init_schema(conn)
    if is_empty(conn):
        load_seed(conn)
    else:
        seed_scoring_defaults(conn)
    return conn


def reset_database() -> None:
    p = db_path()
    if p.exists():
        p.unlink()


# ----------------------------------------------------------------- scoring --
def seed_scoring_defaults(conn: sqlite3.Connection) -> None:
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


def get_weights(conn: sqlite3.Connection) -> Dict[str, float]:
    return {r["key"]: float(r["value"]) for r in conn.execute("SELECT key, value FROM scoring_weights")}


def set_weight(conn: sqlite3.Connection, key: str, value: float) -> None:
    conn.execute(
        "INSERT INTO scoring_weights (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, float(value)),
    )
    conn.commit()


def reset_weights(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM scoring_weights")
    conn.commit()
    seed_scoring_defaults(conn)


def get_tag_weights(conn: sqlite3.Connection) -> Dict[str, float]:
    return {
        r["tag_id"]: float(r["default_weight"] or 0)
        for r in conn.execute("SELECT tag_id, default_weight FROM thesis_tags")
    }


# ------------------------------------------------------------------ reads ---
def q(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=tuple(params))


def fetch_table(conn: sqlite3.Connection, table: str) -> pd.DataFrame:
    return q(conn, f"SELECT * FROM {table}")


def active_round(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    row = conn.execute(
        "SELECT * FROM rounds WHERE is_active = 1 ORDER BY open_date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM rounds ORDER BY rowid LIMIT 1").fetchone()
    return row


# ----------------------------------------------------------------- writes ---
def update_row(conn: sqlite3.Connection, table: str, pk_values: Dict[str, Any], fields: Dict[str, Any]) -> None:
    if not fields:
        return
    cols = table_columns(conn, table)
    fields = {k: v for k, v in fields.items() if k in cols}
    if not fields:
        return
    if "updated_at" in cols:
        fields["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    setters = ", ".join(f"{k} = ?" for k in fields)
    where = " AND ".join(f"{k} = ?" for k in pk_values)
    conn.execute(
        f"UPDATE {table} SET {setters} WHERE {where}",
        (*fields.values(), *pk_values.values()),
    )
    conn.commit()


def insert_row(conn: sqlite3.Connection, table: str, fields: Dict[str, Any]) -> None:
    cols = table_columns(conn, table)
    fields = {k: v for k, v in fields.items() if k in cols}
    names = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(fields.values()))
    conn.commit()


def delete_row(conn: sqlite3.Connection, table: str, pk_values: Dict[str, Any]) -> None:
    where = " AND ".join(f"{k} = ?" for k in pk_values)
    conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(pk_values.values()))
    conn.commit()


# --------------------------------------------------------- conflict engine --
def recompute_conflicts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Re-derive every firm's engagement status from the competitor watchlist.

    Rule: a firm that has backed an active *Blocking* competitor is set to
    "Do Not Engage". A *Review*-severity competitor, or a portfolio row marked
    POTENTIAL_CONFLICT, sets "Hold - Review Conflict". Everything else is
    cleared to "Engage" — unless a human wrote an engagement_reason starting
    with "MANUAL:", which is always left alone.
    """
    blocking = {
        r["competitor_id"]
        for r in conn.execute(
            "SELECT competitor_id FROM competitors WHERE is_active = 1 AND severity = 'Blocking'"
        )
    }
    review = {
        r["competitor_id"]
        for r in conn.execute(
            "SELECT competitor_id FROM competitors WHERE is_active = 1 AND severity = 'Review'"
        )
    }
    # Competitors are matched to portfolio companies by name, case-insensitively.
    comp_by_name = {
        (r["name"] or "").strip().lower(): r["competitor_id"]
        for r in conn.execute("SELECT competitor_id, name FROM competitors WHERE is_active = 1")
    }

    portfolio = conn.execute(
        """
        SELECT fp.firm_id, fp.relevance, pc.name AS company_name
        FROM firm_portfolio fp
        JOIN portfolio_companies pc ON pc.company_id = fp.company_id
        """
    ).fetchall()

    verdicts: Dict[str, str] = {}
    reasons: Dict[str, str] = {}
    for row in portfolio:
        firm_id = row["firm_id"]
        rel = (row["relevance"] or "").upper()
        comp_id = comp_by_name.get((row["company_name"] or "").strip().lower())
        verdict = None
        if rel == "DIRECT_CONFLICT" or (comp_id and comp_id in blocking):
            verdict = "Do Not Engage"
        elif rel == "POTENTIAL_CONFLICT" or (comp_id and comp_id in review):
            verdict = "Hold - Review Conflict"
        if verdict is None:
            continue
        rank = {"Do Not Engage": 2, "Hold - Review Conflict": 1}
        if rank[verdict] > rank.get(verdicts.get(firm_id, ""), 0):
            verdicts[firm_id] = verdict
            reasons[firm_id] = f"Backed {row['company_name']} ({rel.replace('_', ' ').lower()})"

    # Manual overrides recorded on conflict_checks win over the derived value.
    for r in conn.execute(
        "SELECT firm_id, resolution, notes FROM conflict_checks WHERE resolution IS NOT NULL"
    ):
        if r["resolution"] in C.ENGAGEMENT_STATUSES:
            verdicts[r["firm_id"]] = r["resolution"]
            reasons[r["firm_id"]] = r["notes"] or "Set by conflict check"

    counts = {"Engage": 0, "Hold - Review Conflict": 0, "Do Not Engage": 0}
    for r in conn.execute("SELECT firm_id, engagement_reason FROM firms"):
        firm_id = r["firm_id"]
        if (r["engagement_reason"] or "").startswith("MANUAL:"):
            existing = conn.execute(
                "SELECT engagement_status FROM firms WHERE firm_id = ?", (firm_id,)
            ).fetchone()["engagement_status"]
            counts[existing if existing in counts else "Engage"] += 1
            continue
        status = verdicts.get(firm_id, "Engage")
        reason = reasons.get(firm_id, "No conflict found against the active watchlist")
        conn.execute(
            "UPDATE firms SET engagement_status = ?, engagement_reason = ?, "
            "conflict_flag = ?, conflict_note = ? WHERE firm_id = ?",
            (status, reason, 1 if status != "Engage" else 0,
             reason if status != "Engage" else None, firm_id),
        )
        counts[status] += 1
    conn.commit()
    return counts


# ---------------------------------------------------------- import/export ---
def export_table_csv(conn: sqlite3.Connection, table: str) -> bytes:
    df = fetch_table(conn, table)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def export_all_csv(conn: sqlite3.Connection) -> bytes:
    """Every table as one zip — the portable form of your whole CRM."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in SEED_ORDER + ["scoring_weights"]:
            if table_exists(conn, table):
                z.writestr(f"{table}.csv", export_table_csv(conn, table))
    return buf.getvalue()


def import_csv(
    conn: sqlite3.Connection, table: str, file_like, replace: bool = False
) -> Dict[str, Any]:
    df = pd.read_csv(file_like, dtype=str, keep_default_na=False, na_values=[""])
    known = set(table_columns(conn, table))
    unknown = [c for c in df.columns if c not in known]
    n = upsert_dataframe(conn, table, df, replace=replace)
    return {"rows": n, "ignored_columns": unknown}
