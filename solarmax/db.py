"""SQLite persistence helpers."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator

from .config import DEFAULT_POLL_SECONDS, DEFAULT_THEME, DEFAULT_SITE_LAT, DEFAULT_SITE_LON, PROJECT_ROOT

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inverter_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    adapter_kind TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    subnet TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    battery_feed_in_limit_kw REAL NOT NULL DEFAULT 0.0,
    battery_reserve_percent INTEGER NOT NULL DEFAULT 20,
    export_limit_kw REAL NOT NULL DEFAULT 0.0,
    allow_grid_charge INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS power_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    billing_cycle TEXT NOT NULL,
    billing_start_day INTEGER NOT NULL,
    billing_start_month INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tou_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    label TEXT NOT NULL,
    start_minute INTEGER NOT NULL,
    end_minute INTEGER NOT NULL,
    rate_cents_per_kwh REAL NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES power_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telemetry_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inverter_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    solar_kw REAL NOT NULL,
    load_kw REAL NOT NULL,
    grid_import_kw REAL NOT NULL,
    grid_export_kw REAL NOT NULL,
    battery_charge_kw REAL NOT NULL,
    battery_discharge_kw REAL NOT NULL,
    solar_total_kwh REAL NOT NULL,
    load_total_kwh REAL NOT NULL,
    grid_import_total_kwh REAL NOT NULL,
    grid_export_total_kwh REAL NOT NULL,
    battery_charge_total_kwh REAL NOT NULL,
    battery_discharge_total_kwh REAL NOT NULL,
    delta_solar_kwh REAL NOT NULL,
    delta_load_kwh REAL NOT NULL,
    delta_grid_import_kwh REAL NOT NULL,
    delta_grid_export_kwh REAL NOT NULL,
    delta_battery_charge_kwh REAL NOT NULL,
    delta_battery_discharge_kwh REAL NOT NULL,
    FOREIGN KEY(inverter_id) REFERENCES inverter_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telemetry_rollups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inverter_id INTEGER NOT NULL,
    bucket_start TEXT NOT NULL,
    bucket_end TEXT NOT NULL,
    solar_kwh REAL NOT NULL,
    load_kwh REAL NOT NULL,
    grid_import_kwh REAL NOT NULL,
    grid_export_kwh REAL NOT NULL,
    battery_charge_kwh REAL NOT NULL,
    battery_discharge_kwh REAL NOT NULL,
    amount_cents REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY(inverter_id) REFERENCES inverter_profiles(id) ON DELETE CASCADE,
    UNIQUE(inverter_id, bucket_start)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sensible row handling."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        fallback_dir = PROJECT_ROOT / "data"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        path = fallback_dir / path.name
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session(path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and rolls back on failure."""

    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path) -> None:
    """Create tables and seed default settings if the database is empty."""

    with db_session(path) as conn:
        conn.executescript(SCHEMA)
        seed_default_settings(conn)
        seed_default_data(conn)


def seed_default_settings(conn: sqlite3.Connection) -> None:
    """Insert defaults only when no value exists yet."""

    defaults = {
        "theme": DEFAULT_THEME,
        "mode": "manual",
        "poll_interval_seconds": str(DEFAULT_POLL_SECONDS),
        "site_name": "Solarmax",
        "site_lat": str(DEFAULT_SITE_LAT),
        "site_lon": str(DEFAULT_SITE_LON),
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)",
            (key, value),
        )


def seed_default_data(conn: sqlite3.Connection) -> None:
    """Seed the initial inverter and a practical starter plan."""

    inverter_count = conn.execute("SELECT COUNT(*) FROM inverter_profiles").fetchone()[0]
    if inverter_count == 0:
        conn.execute(
            """
            INSERT INTO inverter_profiles
            (name, model, adapter_kind, ip_address, subnet, enabled, battery_feed_in_limit_kw, battery_reserve_percent, export_limit_kw, allow_grid_charge, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Primary SigenStor",
                "SigenStor EC 20.0 TP AU",
                "sigenstor_ec_20_0_tp_au",
                "",
                "",
                1,
                0.0,
                20,
                0.0,
                0,
                "Default first-version inverter profile.",
            ),
        )

    plan_count = conn.execute("SELECT COUNT(*) FROM power_plans").fetchone()[0]
    if plan_count == 0:
        cursor = conn.execute(
            """
            INSERT INTO power_plans
            (provider_name, plan_name, billing_cycle, billing_start_day, billing_start_month, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Example Provider",
                "Solarmax Starter",
                "monthly",
                1,
                1,
                "Replace with your real tariff plan.",
            ),
        )
        plan_id = cursor.lastrowid
        conn.execute("INSERT INTO app_settings(key, value) VALUES('active_plan_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(plan_id),))
        conn.executemany(
            """
            INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (plan_id, "import", "Off-peak", 0, 540, 6.98),
                (plan_id, "import", "Shoulder", 540, 960, 25.3),
                (plan_id, "import", "Peak", 960, 1260, 47.78),
                (plan_id, "import", "Shoulder late", 1260, 1440, 25.3),
                (plan_id, "export", "Solar export", 0, 1440, 8.0),
            ],
        )

    rollup_count = conn.execute("SELECT COUNT(*) FROM telemetry_rollups").fetchone()[0]
    if rollup_count == 0:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        demo_rows = []
        for day_offset in range(7, 0, -1):
            day = (now - timedelta(days=day_offset)).date()
            demo_rows.extend([
                (1, datetime(day.year, day.month, day.day, 6, 0, tzinfo=timezone.utc).isoformat(), datetime(day.year, day.month, day.day, 6, 30, tzinfo=timezone.utc).isoformat(), 0.2, 0.0, 0.18, 0.0, 0.0, 0.0, 0.0),
                (1, datetime(day.year, day.month, day.day, 13, 0, tzinfo=timezone.utc).isoformat(), datetime(day.year, day.month, day.day, 13, 30, tzinfo=timezone.utc).isoformat(), 0.3, 1.4, 0.05, 0.0, 0.0, 0.0, 0.0),
                (1, datetime(day.year, day.month, day.day, 18, 0, tzinfo=timezone.utc).isoformat(), datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc).isoformat(), 0.0, 3.8, 0.0, 0.05, 0.0, 0.0, 0.0),
                (1, datetime(day.year, day.month, day.day, 11, 30, tzinfo=timezone.utc).isoformat(), datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc).isoformat(), 0.0, 0.0, 0.0, 5.2, 0.0, 0.0, 0.0),
            ])
        conn.executemany(
            """
            INSERT INTO telemetry_rollups
            (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh, grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, amount_cents)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            demo_rows,
        )


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """Load all persisted application settings."""

    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Persist a single application setting."""

    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def fetch_all(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Convenience helper returning rows as plain dictionaries."""

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def fetch_one(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Fetch one row as a dictionary if it exists."""

    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def iso_now() -> str:
    """Return a UTC ISO timestamp for storage."""

    return datetime.now(timezone.utc).isoformat()
