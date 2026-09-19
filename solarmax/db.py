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
    reachable INTEGER NOT NULL DEFAULT 0,
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
    daily_supply_charge_cents REAL NOT NULL DEFAULT 0.0,
    export_tier_kwh REAL NOT NULL DEFAULT 0.0,
    export_tier_rate_cents_per_kwh REAL NOT NULL DEFAULT 0.0,
    export_excess_rate_cents_per_kwh REAL NOT NULL DEFAULT 0.0,
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
    export_tier_kwh REAL NOT NULL DEFAULT 0.0,
    export_tier_rate_cents_per_kwh REAL NOT NULL DEFAULT 0.0,
    export_excess_rate_cents_per_kwh REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY(plan_id) REFERENCES power_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS billing_plan_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    billing_cycle TEXT NOT NULL,
    billing_start_day INTEGER NOT NULL,
    billing_start_month INTEGER NOT NULL,
    daily_supply_charge_cents REAL NOT NULL DEFAULT 0.0,
    export_tier_kwh REAL NOT NULL DEFAULT 0.0,
    export_tier_rate_cents_per_kwh REAL NOT NULL DEFAULT 0.0,
    export_excess_rate_cents_per_kwh REAL NOT NULL DEFAULT 0.0,
    notes TEXT NOT NULL DEFAULT '',
    tou_periods_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES power_plans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_billing_plan_revisions_effective
    ON billing_plan_revisions(effective_from, id);

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
    battery_level_percent REAL,
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
    lifetime INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(inverter_id) REFERENCES inverter_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS battery_soc_daily (
    inverter_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    min_percent REAL,
    max_percent REAL,
    first_sample_at TEXT,
    last_sample_at TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0,
    gap_count INTEGER NOT NULL DEFAULT 0,
    max_gap_seconds REAL,
    coverage_status TEXT NOT NULL DEFAULT 'unavailable',
    FOREIGN KEY(inverter_id) REFERENCES inverter_profiles(id) ON DELETE CASCADE,
    PRIMARY KEY(inverter_id, day)
);

CREATE TABLE IF NOT EXISTS daily_counters (
    inverter_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    solar_kwh REAL NOT NULL DEFAULT 0.0,
    load_kwh REAL NOT NULL DEFAULT 0.0,
    grid_import_kwh REAL NOT NULL DEFAULT 0.0,
    grid_export_kwh REAL NOT NULL DEFAULT 0.0,
    grid_import_peak_kwh REAL,
    grid_import_off_peak_kwh REAL,
    grid_export_peak_kwh REAL,
    grid_export_off_peak_kwh REAL,
    battery_charge_kwh REAL NOT NULL DEFAULT 0.0,
    battery_discharge_kwh REAL NOT NULL DEFAULT 0.0,
    solar_baseline_kwh REAL NOT NULL,
    load_baseline_kwh REAL NOT NULL,
    grid_import_baseline_kwh REAL NOT NULL,
    grid_export_baseline_kwh REAL NOT NULL,
    battery_charge_baseline_kwh REAL NOT NULL,
    battery_discharge_baseline_kwh REAL NOT NULL,
    solar_source TEXT NOT NULL DEFAULT 'legacy',
    load_source TEXT NOT NULL DEFAULT 'legacy',
    grid_import_source TEXT NOT NULL DEFAULT 'legacy',
    grid_export_source TEXT NOT NULL DEFAULT 'legacy',
    battery_charge_source TEXT NOT NULL DEFAULT 'legacy',
    battery_discharge_source TEXT NOT NULL DEFAULT 'legacy',
    FOREIGN KEY(inverter_id) REFERENCES inverter_profiles(id) ON DELETE CASCADE,
    UNIQUE(inverter_id, day)
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


# Origin Energy import TOU, represented as half-open local-time intervals.
ORIGIN_IMPORT_PERIODS = (
    ("Shoulder", 0, 540, 25.30),
    ("Off-peak", 540, 960, 6.98),
    ("Peak", 960, 1260, 47.78),
    ("Shoulder", 1260, 1440, 25.30),
)
ORIGIN_IMPORT_TOU_MIGRATION_KEY = "origin_import_tou_v1_applied"
EXPORT_TIER_TOU_MIGRATION_KEY = "export_tiers_to_tou_v1_applied"
BILLING_REVISION_BASELINE = "0001-01-01"


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
        migrate(conn)
        seed_default_settings(conn)
        seed_default_data(conn)
        _ensure_initial_billing_revision(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """Apply lightweight schema migrations to older databases.

    Each migration is idempotent: it checks for the column first and only
    adds it when missing, so re-running init_db is safe.
    """

    columns = {row[1] for row in conn.execute("PRAGMA table_info(inverter_profiles)")}
    if "reachable" not in columns:
        # Default to 0 (unreachable) so the UI shows "—" until the first
        # successful poll proves the inverter is reachable.
        conn.execute("ALTER TABLE inverter_profiles ADD COLUMN reachable INTEGER NOT NULL DEFAULT 0")
    telemetry_columns = {row[1] for row in conn.execute("PRAGMA table_info(telemetry_raw)")}
    if "lifetime" not in telemetry_columns:
        conn.execute("ALTER TABLE telemetry_raw ADD COLUMN lifetime INTEGER NOT NULL DEFAULT 0")
    if "battery_level_percent" not in telemetry_columns:
        conn.execute("ALTER TABLE telemetry_raw ADD COLUMN battery_level_percent REAL")
    daily_counter_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_counters)")}
    for column in (
        "grid_import_peak_kwh", "grid_import_off_peak_kwh",
        "grid_export_peak_kwh", "grid_export_off_peak_kwh",
    ):
        if column not in daily_counter_columns:
            # Historical aggregate counters cannot authoritatively reveal
            # when energy flowed. Keep the split NULL until interval rollups
            # can place it in an explicitly named Peak/Off-peak TOU period.
            conn.execute(f"ALTER TABLE daily_counters ADD COLUMN {column} REAL")
    for key in (
        "solar", "load", "grid_import", "grid_export",
        "battery_charge", "battery_discharge",
    ):
        column = f"{key}_source"
        if column not in daily_counter_columns:
            # Existing baselines used midnight lifetime values. Preserve their
            # accumulated kWh and re-baseline on the next successful poll.
            conn.execute(f"ALTER TABLE daily_counters ADD COLUMN {column} TEXT NOT NULL DEFAULT 'legacy'")
    plan_columns = {row[1] for row in conn.execute("PRAGMA table_info(power_plans)")}
    if "daily_supply_charge_cents" not in plan_columns:
        conn.execute("ALTER TABLE power_plans ADD COLUMN daily_supply_charge_cents REAL NOT NULL DEFAULT 0.0")
    plan_columns = {row[1] for row in conn.execute("PRAGMA table_info(power_plans)")}
    for column in ("export_tier_kwh", "export_tier_rate_cents_per_kwh", "export_excess_rate_cents_per_kwh"):
        if column not in plan_columns:
            conn.execute(f"ALTER TABLE power_plans ADD COLUMN {column} REAL NOT NULL DEFAULT 0.0")
    tou_columns = {row[1] for row in conn.execute("PRAGMA table_info(tou_periods)")}
    for column in ("export_tier_kwh", "export_tier_rate_cents_per_kwh", "export_excess_rate_cents_per_kwh"):
        if column not in tou_columns:
            conn.execute(f"ALTER TABLE tou_periods ADD COLUMN {column} REAL NOT NULL DEFAULT 0.0")
    rollup_columns = {row[1] for row in conn.execute("PRAGMA table_info(telemetry_rollups)")}
    if "pricing_revision_id" not in rollup_columns:
        conn.execute(
            "ALTER TABLE telemetry_rollups ADD COLUMN pricing_revision_id INTEGER REFERENCES billing_plan_revisions(id)"
        )
    _migrate_export_tiers_to_tou_periods(conn)
    _migrate_origin_import_tou(conn)


def create_billing_plan_revision(
    conn: sqlite3.Connection, plan_id: int, effective_from: str
) -> int | None:
    """Append a pricing snapshot unless the effective-date snapshot is unchanged.

    The editable ``power_plans`` and ``tou_periods`` tables remain the
    compatibility surface for the existing UI. Billing reads these immutable
    snapshots instead, so editing the current configuration cannot rewrite
    already priced telemetry.
    """

    plan = fetch_one(conn, "SELECT * FROM power_plans WHERE id=?", (plan_id,))
    if plan is None:
        return None
    periods = fetch_all(
        conn,
        "SELECT * FROM tou_periods WHERE plan_id=? ORDER BY direction, start_minute, id",
        (plan_id,),
    )
    periods_json = json.dumps(periods, sort_keys=True, separators=(",", ":"))
    existing = conn.execute(
        """SELECT * FROM billing_plan_revisions
           WHERE effective_from=? ORDER BY id DESC LIMIT 1""",
        (effective_from,),
    ).fetchone()
    values = (
        int(plan["id"]),
        str(plan["provider_name"]),
        str(plan["plan_name"]),
        str(plan["billing_cycle"]),
        int(plan["billing_start_day"]),
        int(plan["billing_start_month"]),
        float(plan["daily_supply_charge_cents"] or 0.0),
        float(plan["export_tier_kwh"] or 0.0),
        float(plan["export_tier_rate_cents_per_kwh"] or 0.0),
        float(plan["export_excess_rate_cents_per_kwh"] or 0.0),
        str(plan["notes"]),
        periods_json,
    )
    if existing and tuple(existing[column] for column in (
        "plan_id", "provider_name", "plan_name", "billing_cycle",
        "billing_start_day", "billing_start_month", "daily_supply_charge_cents",
        "export_tier_kwh", "export_tier_rate_cents_per_kwh",
        "export_excess_rate_cents_per_kwh", "notes", "tou_periods_json",
    )) == values:
        return int(existing["id"])
    cursor = conn.execute(
        """INSERT INTO billing_plan_revisions
           (plan_id, effective_from, provider_name, plan_name, billing_cycle,
            billing_start_day, billing_start_month, daily_supply_charge_cents,
            export_tier_kwh, export_tier_rate_cents_per_kwh,
            export_excess_rate_cents_per_kwh, notes, tou_periods_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (values[0], effective_from, *values[1:], iso_now()),
    )
    return int(cursor.lastrowid)


def _ensure_initial_billing_revision(conn: sqlite3.Connection) -> None:
    """Backfill one baseline snapshot and link legacy rollups once."""

    active_plan = conn.execute(
        "SELECT value FROM app_settings WHERE key='active_plan_id'"
    ).fetchone()
    baseline_id = conn.execute(
        "SELECT id FROM billing_plan_revisions WHERE effective_from=? ORDER BY id LIMIT 1",
        (BILLING_REVISION_BASELINE,),
    ).fetchone()
    if baseline_id is None and active_plan is not None:
        try:
            baseline = create_billing_plan_revision(
                conn, int(active_plan[0]), BILLING_REVISION_BASELINE
            )
        except (TypeError, ValueError):
            baseline = None
        if baseline is not None:
            baseline_id = {"id": baseline}
    if baseline_id is not None:
        conn.execute(
            "UPDATE telemetry_rollups SET pricing_revision_id=? WHERE pricing_revision_id IS NULL",
            (baseline_id["id"],),
        )


def _migrate_export_tiers_to_tou_periods(conn: sqlite3.Connection) -> None:
    """Attach legacy plan-wide tiers to the plan's Solar export row once.

    The legacy columns deliberately remain for old API clients, but billing now
    reads the period columns. The one-time migration updates only the explicitly
    named Solar export period so it cannot turn a separate battery-export row
    into a tiered tariff.
    """
    if conn.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (EXPORT_TIER_TOU_MIGRATION_KEY,)
    ).fetchone():
        return
    plans = conn.execute(
        """SELECT id, export_tier_kwh, export_tier_rate_cents_per_kwh,
                  export_excess_rate_cents_per_kwh FROM power_plans"""
    ).fetchall()
    for plan in plans:
        tiers = tuple(float(plan[column] or 0.0) for column in (
            "export_tier_kwh",
            "export_tier_rate_cents_per_kwh",
            "export_excess_rate_cents_per_kwh",
        ))
        # A legacy tier is valid only as a complete, positive triplet.  In
        # particular, never migrate an allowance paired with a zero credit.
        if not all(value > 0 for value in tiers):
            continue
        period = conn.execute(
            """SELECT id FROM tou_periods
               WHERE plan_id=? AND direction='export' AND lower(label)='solar export'
               LIMIT 1""",
            (plan["id"],),
        ).fetchone()
        if period:
            conn.execute(
                """UPDATE tou_periods SET export_tier_kwh=?,
                   export_tier_rate_cents_per_kwh=?, export_excess_rate_cents_per_kwh=? WHERE id=?""",
                (*tiers, period["id"]),
            )
    set_setting(conn, EXPORT_TIER_TOU_MIGRATION_KEY, "1")


def _migrate_origin_import_tou(conn: sqlite3.Connection) -> None:
    """Apply the approved Origin import TOU to the current active plan once.

    The migration deliberately changes only that plan's import periods.  Its
    plan metadata, export periods, and tiered export settings are retained.
    """

    applied = conn.execute(
        "SELECT value FROM app_settings WHERE key=?", (ORIGIN_IMPORT_TOU_MIGRATION_KEY,)
    ).fetchone()
    if applied:
        return
    active_plan = conn.execute(
        "SELECT value FROM app_settings WHERE key='active_plan_id'"
    ).fetchone()
    if active_plan is None:
        return
    try:
        plan_id = int(active_plan[0])
    except (TypeError, ValueError):
        return
    if conn.execute("SELECT 1 FROM power_plans WHERE id=?", (plan_id,)).fetchone() is None:
        return

    conn.execute("DELETE FROM tou_periods WHERE plan_id=? AND direction='import'", (plan_id,))
    conn.executemany(
        """INSERT INTO tou_periods
           (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh)
           VALUES (?, 'import', ?, ?, ?, ?)""",
        [(plan_id, *period) for period in ORIGIN_IMPORT_PERIODS],
    )
    set_setting(conn, ORIGIN_IMPORT_TOU_MIGRATION_KEY, "1")


def seed_default_settings(conn: sqlite3.Connection) -> None:
    """Insert defaults only when no value exists yet."""

    defaults = {
        "theme": DEFAULT_THEME,
        "mode": "manual",
        "poll_interval_seconds": str(DEFAULT_POLL_SECONDS),
        "site_name": "Solarmax",
        "site_lat": str(DEFAULT_SITE_LAT),
        "site_lon": str(DEFAULT_SITE_LON),
        "site_timezone": "Australia/Brisbane",
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
            (provider_name, plan_name, billing_cycle, billing_start_day, billing_start_month, daily_supply_charge_cents, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Example Provider",
                "Solarmax Starter",
                "monthly",
                1,
                1,
                0.0,
                "Replace with your real tariff plan.",
            ),
        )
        plan_id = cursor.lastrowid
        conn.execute("INSERT INTO app_settings(key, value) VALUES('active_plan_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(plan_id),))
        conn.executemany(
            """
            INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh, export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                *[(plan_id, "import", *period, 0.0, 0.0, 0.0) for period in ORIGIN_IMPORT_PERIODS],
                (plan_id, "export", "Solar export", 0, 1440, 3.0, 8.0, 8.0, 3.0),
            ],
        )
        conn.execute(
            "UPDATE power_plans SET export_tier_kwh=8.0, export_tier_rate_cents_per_kwh=8.0, export_excess_rate_cents_per_kwh=3.0 WHERE id=?",
            (plan_id,),
        )
        set_setting(conn, ORIGIN_IMPORT_TOU_MIGRATION_KEY, "1")

    # Remove the original fixed demo pattern even in existing databases.  Its
    # energy was fabricated and must never contribute to a real bill.
    conn.execute(
        """
        DELETE FROM telemetry_rollups
        WHERE inverter_id = 1
          AND (grid_import_kwh = 0.18 OR grid_import_kwh = 0.05 OR grid_export_kwh IN (0.05, 5.2))
          AND ((grid_import_kwh = 0.18 AND solar_kwh = 0.2 AND load_kwh = 0.0)
            OR (grid_import_kwh = 0.05 AND solar_kwh = 0.3 AND load_kwh = 1.4)
            OR (grid_export_kwh = 0.05 AND solar_kwh = 0.0 AND load_kwh = 3.8)
            OR (grid_export_kwh = 5.2 AND solar_kwh = 0.0 AND load_kwh = 0.0))
        """
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
