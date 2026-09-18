"""Application service layer for dashboard, polling, and updates."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .billing import aggregate_bill_lines, apply_daily_export_tier, bucket_start, current_billing_window, find_period, rollup_by_day_and_period
from .db import (
    create_billing_plan_revision,
    db_session,
    fetch_all,
    fetch_one,
    get_settings,
    init_db,
    set_setting,
)
from .inverters.base import InverterReading
from .inverters.registry import get_adapter
from .models import AppSettings, InverterProfile, PowerPlan, TouPeriod
from .weather import WeatherSummary, fetch_open_meteo, recommend_battery_policy

SUPPORTED_THEMES = {"classic-light", "classic-dark", "deep-ocean", "ember-core"}
RETIRED_THEMES = {"solar-glass", "midnight-neon", "warm-desert"}

logger = logging.getLogger(__name__)


class PlanDeletionError(Exception):
    """A plan cannot be safely removed without changing billing history."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SolarmaxService:
    """Coordinates persistence, polling, billing, and AI helpers."""

    _DAILY_COUNTER_KEYS = (
        "solar", "load", "grid_import", "grid_export",
        "battery_charge", "battery_discharge",
    )
    _TOTAL_COLUMNS = {
        "solar": "solar_total_kwh",
        "load": "load_total_kwh",
        "grid_import": "grid_import_total_kwh",
        "grid_export": "grid_export_total_kwh",
        "battery_charge": "battery_charge_total_kwh",
        "battery_discharge": "battery_discharge_total_kwh",
    }

    def __init__(self, db_path: Path):
        self.db_path = db_path
        init_db(db_path)
        self._refresh_daily_tou_counters()

    # ---------------------------------------------------------------------
    # Settings and configuration
    # ---------------------------------------------------------------------

    def load_app_settings(self) -> AppSettings:
        with db_session(self.db_path) as conn:
            raw = get_settings(conn)
        theme = raw.get("theme", "classic-dark")
        # Existing installations may still have one of the retired themes in
        # SQLite. Normalize before Pydantic validates the new ThemeName literal.
        if theme in RETIRED_THEMES or theme not in SUPPORTED_THEMES:
            theme = "classic-dark"
        return AppSettings(
            theme=theme,
            mode=raw.get("mode", "manual"),
            poll_interval_seconds=int(raw.get("poll_interval_seconds", "30")),
            site_name=raw.get("site_name", "Solarmax"),
            site_lat=float(raw.get("site_lat", "-27.4698")),
            site_lon=float(raw.get("site_lon", "153.0251")),
            site_timezone=self._valid_timezone(raw.get("site_timezone", "Australia/Brisbane")),
            active_plan_id=int(raw["active_plan_id"]) if raw.get("active_plan_id") else None,
        )

    def save_app_settings(self, payload: dict[str, Any]) -> None:
        settings = self.load_app_settings().model_dump()
        settings.update({k: v for k, v in payload.items() if v is not None or k == "active_plan_id"})
        settings = AppSettings(**settings).model_dump()
        # Settings are a complete persisted configuration.  A selected plan
        # must exist; an explicit None is the supported way to clear it.
        if settings.get("active_plan_id") is not None and not self.get_power_plan(int(settings["active_plan_id"])):
            raise KeyError(f"No power plan with id {settings['active_plan_id']}")
        with db_session(self.db_path) as conn:
            settings["site_timezone"] = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            for key in ("theme", "mode", "site_name", "site_lat", "site_lon", "site_timezone", "poll_interval_seconds"):
                if key in settings:
                    set_setting(conn, key, str(settings[key]))
            set_setting(conn, "active_plan_id", "" if settings.get("active_plan_id") is None else str(settings["active_plan_id"]))
            if settings.get("active_plan_id") is not None:
                self._schedule_billing_revision(
                    conn,
                    int(settings["active_plan_id"]),
                    site_timezone=settings["site_timezone"],
                )

    def list_inverters(self) -> list[dict[str, Any]]:
        with db_session(self.db_path) as conn:
            return fetch_all(conn, "SELECT * FROM inverter_profiles ORDER BY id")

    def get_inverter(self, inverter_id: int) -> dict[str, Any] | None:
        with db_session(self.db_path) as conn:
            return fetch_one(conn, "SELECT * FROM inverter_profiles WHERE id = ?", (inverter_id,))

    def get_power_plan(self, plan_id: int) -> dict[str, Any] | None:
        with db_session(self.db_path) as conn:
            return fetch_one(conn, "SELECT * FROM power_plans WHERE id = ?", (plan_id,))

    def upsert_inverter(self, payload: dict[str, Any]) -> int:
        profile = InverterProfile(**payload)
        with db_session(self.db_path) as conn:
            if profile.id:
                updated = conn.execute(
                    """
                    UPDATE inverter_profiles
                    SET name=?, model=?, adapter_kind=?, ip_address=?, subnet=?, enabled=?, battery_feed_in_limit_kw=?, battery_reserve_percent=?, export_limit_kw=?, allow_grid_charge=?, notes=?
                    WHERE id=?
                    """,
                    (
                        profile.name,
                        profile.model,
                        profile.adapter_kind,
                        profile.ip_address,
                        profile.subnet,
                        int(profile.enabled),
                        profile.battery_feed_in_limit_kw,
                        profile.battery_reserve_percent,
                        profile.export_limit_kw,
                        int(profile.allow_grid_charge),
                        profile.notes,
                        profile.id,
                    ),
                )
                if not updated.rowcount:
                    raise KeyError(f"No inverter with id {profile.id}")
                return profile.id
            cur = conn.execute(
                """
                INSERT INTO inverter_profiles
                (name, model, adapter_kind, ip_address, subnet, enabled, battery_feed_in_limit_kw, battery_reserve_percent, export_limit_kw, allow_grid_charge, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.name,
                    profile.model,
                    profile.adapter_kind,
                    profile.ip_address,
                    profile.subnet,
                    int(profile.enabled),
                    profile.battery_feed_in_limit_kw,
                    profile.battery_reserve_percent,
                    profile.export_limit_kw,
                    int(profile.allow_grid_charge),
                    profile.notes,
                ),
            )
            return int(cur.lastrowid)

    def list_power_plans(self) -> list[dict[str, Any]]:
        with db_session(self.db_path) as conn:
            return fetch_all(conn, "SELECT * FROM power_plans ORDER BY id")

    def upsert_power_plan(self, payload: dict[str, Any]) -> int:
        plan = PowerPlan(**payload)
        with db_session(self.db_path) as conn:
            if plan.id:
                updated = conn.execute(
                    """
                    UPDATE power_plans
                    SET provider_name=?, plan_name=?, billing_cycle=?, billing_start_day=?, billing_start_month=?, daily_supply_charge_cents=?, export_tier_kwh=?, export_tier_rate_cents_per_kwh=?, export_excess_rate_cents_per_kwh=?, notes=?
                    WHERE id=?
                    """,
                    (
                        plan.provider_name,
                        plan.plan_name,
                        plan.billing_cycle,
                        plan.billing_start_day,
                        plan.billing_start_month,
                        plan.daily_supply_charge_cents,
                        plan.export_tier_kwh,
                        plan.export_tier_rate_cents_per_kwh,
                        plan.export_excess_rate_cents_per_kwh,
                        plan.notes,
                        plan.id,
                    ),
                )
                if not updated.rowcount:
                    raise KeyError(f"No power plan with id {plan.id}")
                if self._active_plan_id(conn) == plan.id:
                    self._schedule_billing_revision(conn, plan.id)
                return plan.id
            cur = conn.execute(
                """
                INSERT INTO power_plans
                (provider_name, plan_name, billing_cycle, billing_start_day, billing_start_month, daily_supply_charge_cents, export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.provider_name,
                    plan.plan_name,
                    plan.billing_cycle,
                    plan.billing_start_day,
                    plan.billing_start_month,
                    plan.daily_supply_charge_cents,
                    plan.export_tier_kwh,
                    plan.export_tier_rate_cents_per_kwh,
                    plan.export_excess_rate_cents_per_kwh,
                    plan.notes,
                ),
            )
            return int(cur.lastrowid)

    def delete_power_plan(self, plan_id: int) -> None:
        """Delete an unused plan without touching billing or telemetry history."""

        with db_session(self.db_path) as conn:
            if not fetch_one(conn, "SELECT id FROM power_plans WHERE id=?", (plan_id,)):
                raise KeyError(f"No power plan with id {plan_id}")
            if self._active_plan_id(conn) == plan_id:
                raise PlanDeletionError("active_plan", "The active power plan cannot be deleted")
            if int(fetch_one(conn, "SELECT COUNT(*) AS count FROM power_plans")["count"]) <= 1:
                raise PlanDeletionError("last_plan", "The last remaining power plan cannot be deleted")
            if fetch_one(
                conn,
                """SELECT 1 FROM telemetry_rollups tr
                   JOIN billing_plan_revisions bpr ON bpr.id = tr.pricing_revision_id
                   WHERE bpr.plan_id=? LIMIT 1""",
                (plan_id,),
            ):
                raise PlanDeletionError("telemetry_references", "The power plan has telemetry references and cannot be deleted")
            if fetch_one(conn, "SELECT 1 FROM billing_plan_revisions WHERE plan_id=? LIMIT 1", (plan_id,)):
                raise PlanDeletionError("billing_plan_revisions", "The power plan has billing history and cannot be deleted")
            conn.execute("DELETE FROM power_plans WHERE id=?", (plan_id,))

    def update_daily_supply_charge(self, plan_id: int, cents: float) -> None:
        """Update only the fixed daily charge for an existing plan."""
        validated = PowerPlan(provider_name="Existing", plan_name="Existing", daily_supply_charge_cents=cents)
        with db_session(self.db_path) as conn:
            conn.execute("UPDATE power_plans SET daily_supply_charge_cents=? WHERE id=?", (validated.daily_supply_charge_cents, plan_id))
            if self._active_plan_id(conn) == plan_id:
                self._schedule_billing_revision(conn, plan_id)

    def correct_billing_revision_supply_charge(
        self, plan_id: int, effective_from: str, cents: float
    ) -> None:
        """Correct one dated immutable revision without repricing other days."""
        validated = PowerPlan(
            provider_name="Existing", plan_name="Existing", daily_supply_charge_cents=cents
        )
        with db_session(self.db_path) as conn:
            updated = conn.execute(
                """UPDATE billing_plan_revisions
                   SET daily_supply_charge_cents=?
                   WHERE plan_id=? AND effective_from=?""",
                (validated.daily_supply_charge_cents, plan_id, effective_from),
            )
            if not updated.rowcount:
                raise KeyError(
                    f"No billing revision for plan {plan_id} effective {effective_from}"
                )
        self.recalculate_rollup_amounts()

    def list_tou_periods(self, plan_id: int) -> list[dict[str, Any]]:
        with db_session(self.db_path) as conn:
            return fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ? ORDER BY direction, start_minute", (plan_id,))

    def replace_tou_periods(self, plan_id: int, periods: list[dict[str, Any]]) -> None:
        # IDs and plan ownership come from persistence/the route, never client
        # payload. This also keeps callers using rows returned from the database
        # from passing duplicate keyword arguments to TouPeriod.
        validated = [
            TouPeriod(plan_id=plan_id, **{key: value for key, value in period.items() if key not in {"id", "plan_id"}})
            for period in periods
        ]
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM tou_periods WHERE plan_id = ?", (plan_id,))
            conn.executemany(
                """
                INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh, export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (plan_id, period.direction, period.label, period.start_minute, period.end_minute, period.rate_cents_per_kwh,
                     period.export_tier_kwh if period.direction == "export" else 0.0,
                     period.export_tier_rate_cents_per_kwh if period.direction == "export" else 0.0,
                     period.export_excess_rate_cents_per_kwh if period.direction == "export" else 0.0)
                    for period in validated
                ],
            )
            if self._active_plan_id(conn) == plan_id:
                self._schedule_billing_revision(conn, plan_id)

    def save_tou_schedule(
        self,
        plan_id: int,
        periods: list[dict[str, Any]],
        daily_supply_charge_cents: float,
        export_tier_kwh: float | None = None,
        export_tier_rate_cents_per_kwh: float | None = None,
        export_excess_rate_cents_per_kwh: float | None = None,
    ) -> None:
        """Replace a plan's schedule and tariff values in one transaction."""

        if any(value is not None for value in (
            export_tier_kwh,
            export_tier_rate_cents_per_kwh,
            export_excess_rate_cents_per_kwh,
        )):
            raise ValueError(
                "Plan-wide export tier fields are deprecated; configure tiers on export TOU periods"
            )

        validated_periods = [
            TouPeriod(plan_id=plan_id, **{key: value for key, value in period.items() if key not in {"id", "plan_id"}})
            for period in periods
        ]
        # Validate the charge before deleting the existing schedule.
        tariff = PowerPlan(provider_name="Existing", plan_name="Existing", daily_supply_charge_cents=daily_supply_charge_cents)
        with db_session(self.db_path) as conn:
            if not fetch_one(conn, "SELECT id FROM power_plans WHERE id=?", (plan_id,)):
                raise KeyError(f"No power plan with id {plan_id}")
            conn.execute("DELETE FROM tou_periods WHERE plan_id = ?", (plan_id,))
            conn.executemany(
                """INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh,
                       export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (plan_id, period.direction, period.label, period.start_minute, period.end_minute, period.rate_cents_per_kwh,
                     period.export_tier_kwh if period.direction == "export" else 0.0,
                     period.export_tier_rate_cents_per_kwh if period.direction == "export" else 0.0,
                     period.export_excess_rate_cents_per_kwh if period.direction == "export" else 0.0)
                    for period in validated_periods
                ],
            )
            conn.execute(
                "UPDATE power_plans SET daily_supply_charge_cents=? WHERE id=?",
                (tariff.daily_supply_charge_cents, plan_id),
            )
            if self._active_plan_id(conn) == plan_id:
                self._schedule_billing_revision(conn, plan_id)

    # ---------------------------------------------------------------------
    # Polling and telemetry
    # ---------------------------------------------------------------------

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll each enabled inverter and persist a new raw telemetry row.

        When an adapter returns None (inverter unreachable), no telemetry row
        is written and the inverter's reachability flag is set to 0. When a
        real reading succeeds, the flag is set back to 1 so the UI can show
        normal values again.
        """

        self.wipe_stale_days()
        results: list[dict[str, Any]] = []
        with db_session(self.db_path) as conn:
            inverters = fetch_all(conn, "SELECT * FROM inverter_profiles WHERE enabled = 1 ORDER BY id")
            for inverter in inverters:
                adapter = get_adapter(inverter["adapter_kind"])
                previous_row = fetch_one(
                    conn,
                    "SELECT * FROM telemetry_raw WHERE inverter_id = ? ORDER BY id DESC LIMIT 1",
                    (inverter["id"],),
                )
                previous = self._row_to_reading(previous_row) if previous_row else None

                reading = adapter.read(inverter, previous)
                if reading is None:
                    # Inverter unreachable: record the state and skip telemetry.
                    conn.execute(
                        "UPDATE inverter_profiles SET reachable = 0 WHERE id = ?",
                        (inverter["id"],),
                    )
                    results.append({"inverter": inverter, "reading": None})
                    continue

                # Real reading: mark reachable and persist telemetry.
                conn.execute(
                    "UPDATE inverter_profiles SET reachable = 1 WHERE id = ?",
                    (inverter["id"],),
                )
                # Lifetime counters and legacy integrated totals are different
                # sources.  The transition itself is a fresh baseline.
                deltas = self._deltas_for_reading(reading, previous)
                conn.execute(
                    """
                    INSERT INTO telemetry_raw
                    (inverter_id, captured_at, solar_kw, load_kw, grid_import_kw, grid_export_kw, battery_charge_kw, battery_discharge_kw,
                     solar_total_kwh, load_total_kwh, grid_import_total_kwh, grid_export_total_kwh, battery_charge_total_kwh, battery_discharge_total_kwh,
                     delta_solar_kwh, delta_load_kwh, delta_grid_import_kwh, delta_grid_export_kwh, delta_battery_charge_kwh, delta_battery_discharge_kwh, lifetime)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inverter["id"],
                        reading.captured_at.isoformat(),
                        reading.solar_kw,
                        reading.load_kw,
                        reading.grid_import_kw,
                        reading.grid_export_kw,
                        reading.battery_charge_kw,
                        reading.battery_discharge_kw,
                        reading.solar_total_kwh,
                        reading.load_total_kwh,
                        reading.grid_import_total_kwh,
                        reading.grid_export_total_kwh,
                        reading.battery_charge_total_kwh,
                        reading.battery_discharge_total_kwh,
                        deltas["delta_solar_kwh"],
                        deltas["delta_load_kwh"],
                        deltas["delta_grid_import_kwh"],
                        deltas["delta_grid_export_kwh"],
                        deltas["delta_battery_charge_kwh"],
                        deltas["delta_battery_discharge_kwh"],
                        int(reading.lifetime),
                    ),
                )
                self._update_daily_counters(conn, inverter["id"], reading)
                results.append({"inverter": inverter, "reading": self._reading_to_dict(reading, deltas)})
        self.rollup_completed_buckets()
        return results

    def latest_live_state(self) -> list[dict[str, Any]]:
        """Return the most recent telemetry row per inverter with reachability.

        The join against inverter_profiles adds the ``reachable`` flag so the
        dashboard can tell whether a row is live or stale (inverter currently
        unreachable).
        """

        with db_session(self.db_path) as conn:
            rows = fetch_all(
                conn,
                """
                SELECT tr.*, ip.reachable AS reachable
                FROM telemetry_raw tr
                JOIN (
                    SELECT inverter_id, MAX(id) AS max_id
                    FROM telemetry_raw
                    GROUP BY inverter_id
                ) latest ON latest.max_id = tr.id
                JOIN inverter_profiles ip ON ip.id = tr.inverter_id
                ORDER BY tr.inverter_id
                """,
            )
        return rows

    def rollup_completed_buckets(self) -> None:
        """Aggregate any half-hour bucket that has now closed."""

        with db_session(self.db_path) as conn:
            settings = get_settings(conn)
            site_timezone = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            rows = fetch_all(conn, "SELECT * FROM telemetry_raw ORDER BY captured_at")
            by_bucket: dict[tuple[int, str], list[dict[str, Any]]] = {}
            observed_buckets: set[tuple[int, str]] = set()
            for row in rows:
                captured = datetime.fromisoformat(row["captured_at"])
                start = bucket_start(captured).isoformat()
                key = (row["inverter_id"], start)
                observed_buckets.add(key)
                # Only plant lifetime-counter deltas are meter readings.  Old
                # session/integrated rows have no usable grid-meter provenance
                # and must never become billable energy.
                if int(row["lifetime"]):
                    by_bucket.setdefault(key, []).append(row)
            active_cutoff = bucket_start(datetime.now(timezone.utc))
            for inverter_id, start_iso in observed_buckets - set(by_bucket):
                start = datetime.fromisoformat(start_iso)
                if start + timedelta(minutes=30) <= active_cutoff:
                    conn.execute(
                        "DELETE FROM telemetry_rollups WHERE inverter_id = ? AND bucket_start = ?",
                        (inverter_id, start_iso),
                    )
            for (inverter_id, start_iso), bucket_rows in by_bucket.items():
                start = datetime.fromisoformat(start_iso)
                end = start + timedelta(minutes=30)
                if end > active_cutoff:
                    continue
                sums = {k: 0.0 for k in ("delta_solar_kwh", "delta_load_kwh", "delta_grid_import_kwh", "delta_grid_export_kwh", "delta_battery_charge_kwh", "delta_battery_discharge_kwh")}
                for row in bucket_rows:
                    for key in sums:
                        sums[key] += float(row[key])
                pricing_revision = self._revision_for_day(
                    conn, self._local_day(start_iso, ZoneInfo(site_timezone))
                )
                conn.execute(
                    """
                    INSERT INTO telemetry_rollups
                    (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh, grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, amount_cents, pricing_revision_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(inverter_id, bucket_start) DO UPDATE SET
                        bucket_end=excluded.bucket_end,
                        solar_kwh=excluded.solar_kwh,
                        load_kwh=excluded.load_kwh,
                        grid_import_kwh=excluded.grid_import_kwh,
                        grid_export_kwh=excluded.grid_export_kwh,
                        battery_charge_kwh=excluded.battery_charge_kwh,
                        battery_discharge_kwh=excluded.battery_discharge_kwh,
                        amount_cents=excluded.amount_cents,
                        pricing_revision_id=COALESCE(telemetry_rollups.pricing_revision_id, excluded.pricing_revision_id)
                    """,
                    (
                        inverter_id,
                        start_iso,
                        end.isoformat(),
                        sums["delta_solar_kwh"],
                        sums["delta_load_kwh"],
                        sums["delta_grid_import_kwh"],
                        sums["delta_grid_export_kwh"],
                        sums["delta_battery_charge_kwh"],
                        sums["delta_battery_discharge_kwh"],
                        0.0,
                        pricing_revision["id"] if pricing_revision else None,
                    ),
                )
        self.recalculate_rollup_amounts()

    def recalculate_rollup_amounts(self) -> None:
        """Re-price rollups from their immutable pricing snapshots."""

        with db_session(self.db_path) as conn:
            settings = get_settings(conn)
            plan_id = int(settings["active_plan_id"]) if settings.get("active_plan_id") else None
            site_timezone = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            fallback_periods = (
                fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
                if plan_id else []
            )
            rollups = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
            for row in rollups:
                captured = datetime.fromisoformat(row["bucket_start"])
                revision_id, periods = self._pricing_for_rollup(
                    conn, row, site_timezone, fallback_periods
                )
                if not periods:
                    continue
                import_period = self._match_period(periods, captured, "import", site_timezone)
                export_period = self._match_period(periods, captured, "export", site_timezone)
                amount = 0.0
                if import_period:
                    amount += float(row["grid_import_kwh"]) * float(import_period["rate_cents_per_kwh"])
                if export_period:
                    amount -= float(row["grid_export_kwh"]) * float(export_period["rate_cents_per_kwh"])
                conn.execute(
                    "UPDATE telemetry_rollups SET amount_cents=?, pricing_revision_id=? WHERE id=?",
                    (amount, revision_id, row["id"]),
                )
        self._refresh_daily_tou_counters()

    def _refresh_daily_tou_counters(self) -> None:
        """Persist only authoritative Peak/Off-peak interval quantities.

        Aggregate daily meters and reconciliation rows have no time-of-use
        provenance, so they must not be copied into these nullable columns.
        Flat, shoulder, or custom-labelled periods likewise remain unassigned.
        """

        with db_session(self.db_path) as conn:
            settings = get_settings(conn)
            site_timezone = self._valid_timezone(
                settings.get("site_timezone", "Australia/Brisbane")
            )
            plan_id = self._active_plan_id(conn)
            fallback_periods = (
                fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
                if plan_id else []
            )
            conn.execute(
                """UPDATE daily_counters SET
                   grid_import_peak_kwh=NULL, grid_import_off_peak_kwh=NULL,
                   grid_export_peak_kwh=NULL, grid_export_off_peak_kwh=NULL"""
            )
            totals: dict[tuple[int, str], dict[str, float]] = {}
            for row in fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start"):
                captured = datetime.fromisoformat(row["bucket_start"])
                _, periods = self._pricing_for_rollup(
                    conn, row, site_timezone, fallback_periods
                )
                day = captured.astimezone(ZoneInfo(site_timezone)).date().isoformat()
                values = totals.setdefault((int(row["inverter_id"]), day), {})
                for direction in ("import", "export"):
                    period = self._match_period(periods, captured, direction, site_timezone)
                    if period is None:
                        continue
                    label = str(period.get("label", "")).strip().casefold().replace("_", "-")
                    if label.startswith(("off-peak", "off peak", "offpeak")):
                        bucket = "off_peak"
                    elif label.startswith("peak"):
                        bucket = "peak"
                    else:
                        continue
                    column = f"grid_{direction}_{bucket}_kwh"
                    values[column] = values.get(column, 0.0) + float(row[f"grid_{direction}_kwh"])
            columns = (
                "grid_import_peak_kwh", "grid_import_off_peak_kwh",
                "grid_export_peak_kwh", "grid_export_off_peak_kwh",
            )
            for (inverter_id, day), values in totals.items():
                conn.execute(
                    f"""UPDATE daily_counters SET
                        {', '.join(f'{column}=?' for column in columns)}
                        WHERE inverter_id=? AND day=?""",
                    tuple(
                        round(values[column], 6) if column in values else None
                        for column in columns
                    ) + (inverter_id, day),
                )

    def close_day(self) -> dict[str, Any]:
        """Close the current local day and return its finalized meter totals."""

        settings = self.load_app_settings()
        site_timezone = settings.site_timezone
        day = datetime.now(ZoneInfo(site_timezone)).date().isoformat()

        # Daily counters are updated as each successful reading is persisted.
        # Closing explicitly completes already-ended interval buckets and
        # re-prices them before reporting the authoritative meter totals.
        self.rollup_completed_buckets()
        self.recalculate_rollup_amounts()
        with db_session(self.db_path) as conn:
            totals = self._metered_day_totals(conn, day)
            solar = self._daily_counter_total(conn, day, "solar_kwh")

        logger.info(
            "Day closed for %s: solar=%s kWh, export=%s kWh, import=%s kWh",
            day,
            solar,
            totals["grid_export_kwh"],
            totals["grid_import_kwh"],
        )
        return {
            "day": day,
            "solar_kwh": solar,
            "grid_export_kwh": totals["grid_export_kwh"],
            "grid_import_kwh": totals["grid_import_kwh"],
        }

    def wipe_stale_days(self) -> None:
        """Discard stale yesterday/current telemetry when yesterday has no meter row.

        A missing prior-day daily counter means the raw lifetime-counter stream
        cannot safely bridge midnight.  Keep daily counters themselves, which
        remain the plant-meter authority, and let the next poll establish a
        fresh raw baseline.
        """

        settings = self.load_app_settings()
        site_zone = ZoneInfo(settings.site_timezone)
        today = datetime.now(site_zone).date()
        stale_days = {today - timedelta(days=1), today}
        yesterday = (today - timedelta(days=1)).isoformat()
        with db_session(self.db_path) as conn:
            exists = fetch_one(
                conn,
                "SELECT 1 AS present FROM daily_counters WHERE day = ? LIMIT 1",
                (yesterday,),
            )
            if exists:
                return

            raw_ids: list[int] = []
            for row in fetch_all(conn, "SELECT id, captured_at FROM telemetry_raw"):
                captured = datetime.fromisoformat(row["captured_at"])
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=timezone.utc)
                local_captured = captured.astimezone(site_zone)
                # A sample exactly at local midnight is the fresh baseline for
                # the new day. Retaining it lets the following poll recover
                # today's genuine lifetime-meter delta after a stale cleanup.
                if local_captured.date() in stale_days and not (
                    local_captured.date() == today and local_captured.time() == time.min
                ):
                    raw_ids.append(int(row["id"]))
            rollup_ids = [
                int(row["id"])
                for row in fetch_all(conn, "SELECT id, bucket_start FROM telemetry_rollups")
                if self._local_day(row["bucket_start"], site_zone) in stale_days
            ]
            if raw_ids:
                conn.executemany("DELETE FROM telemetry_raw WHERE id = ?", ((row_id,) for row_id in raw_ids))
            if rollup_ids:
                conn.executemany("DELETE FROM telemetry_rollups WHERE id = ?", ((row_id,) for row_id in rollup_ids))
        if raw_ids or rollup_ids:
            logger.warning(
                "Wiped %s raw and %s rollup telemetry rows for %s and %s because no daily counter exists for %s",
                len(raw_ids), len(rollup_ids), today - timedelta(days=1), today, yesterday,
            )

    def current_bill_summary(self) -> dict[str, Any]:
        """Compute the current billing summary and line breakdown."""

        with db_session(self.db_path) as conn:
            settings = get_settings(conn)
            plan_id = int(settings["active_plan_id"]) if settings.get("active_plan_id") else None
            site_timezone = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            if not plan_id:
                daily_site_totals = self._daily_site_totals(conn, [])
                return self._empty_bill_summary(daily_site_totals, site_timezone)
            plan = fetch_one(conn, "SELECT * FROM power_plans WHERE id = ?", (plan_id,))
            if plan is None:
                daily_site_totals = self._daily_site_totals(conn, [])
                return self._empty_bill_summary(daily_site_totals, site_timezone)
            fallback_periods = fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
            rollups = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
            detailed = self._rollup_bill_lines(
                conn, rollups, site_timezone, fallback_periods
            )
            _, today_periods, today_supply_charge = self._pricing_for_today(
                conn,
                site_timezone,
                fallback_periods,
                float(plan.get("daily_supply_charge_cents", 0.0) or 0.0),
            )
            today_revision = self._revision_for_day(
                conn, datetime.now(ZoneInfo(site_timezone)).date()
            )
            detailed.extend(self._current_live_lines(
                conn,
                today_periods,
                site_timezone,
                int(today_revision["id"]) if today_revision else None,
            ))
            # The plant daily counter total is the meter authority.  Interval
            # rows retain their TOU allocation where observed; if polling did
            # not have a pre-midnight lifetime baseline, report the remaining
            # meter energy without fabricating a time/rate for it.
            grouped, today_metered = self._with_meter_reconciliation(
                conn, detailed, site_timezone, fallback_periods,
            )
            grouped = apply_daily_export_tier(grouped)
            billing_start, billing_end = current_billing_window(
                datetime.now(ZoneInfo(site_timezone)).date(),
                str(plan.get("billing_cycle") or "monthly"),
                int(plan.get("billing_start_day") or 1),
                int(plan.get("billing_start_month") or 1),
            )
            daily = list(grouped)
            supply_days = sorted({row["day"] for row in grouped})
            for day in supply_days:
                revision_id, supply_charge = self._supply_charge_for_day(
                    conn,
                    day,
                    grouped,
                    site_timezone,
                    float(plan.get("daily_supply_charge_cents", 0.0) or 0.0),
                )
                grouped.append({
                    "day": day,
                    "period_label": "Daily supply charge",
                    "direction": "fixed",
                    "kwh": 0.0,
                    "rate_cents_per_kwh": 0.0,
                    "amount_cents": round(supply_charge, 3),
                    "pricing_revision_id": revision_id,
                })
            current_cycle_rows = [
                row for row in grouped
                if billing_start <= date.fromisoformat(row["day"]) <= billing_end
            ]
            total = sum(float(row["amount_cents"]) for row in current_cycle_rows if row["amount_cents"] is not None)
            daily_site_totals = self._daily_site_totals(conn, grouped)
            return {
                "plan": plan,
                "total_cents": round(total, 2),
                "rows": grouped,
                "daily": daily,
                "daily_site_totals": daily_site_totals,
                "today_grid_import_kwh": round(today_metered["grid_import_kwh"], 4),
                "today_grid_export_kwh": round(today_metered["grid_export_kwh"], 4),
                "supply_charge_cents": today_supply_charge,
                "supply_charge_days": sum(
                    date.fromisoformat(day) >= billing_start
                    and date.fromisoformat(day) <= billing_end
                    for day in supply_days
                ),
                "billing_window_applied": True,
                "lines": [],
                "rollups": [],
            }

    def daily_bill_breakdown(self, conn: sqlite3.Connection, plan_id: int, site_timezone: str | None = None) -> list[dict[str, Any]]:
        """Return the per-day/per-period breakdown for the current bill screen."""

        periods = fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
        site_timezone = site_timezone or self.load_app_settings().site_timezone
        rollups = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
        detailed = self._rollup_bill_lines(conn, rollups, site_timezone, periods)
        today_revision = self._revision_for_day(
            conn, datetime.now(ZoneInfo(site_timezone)).date()
        )
        _, today_periods, _ = self._pricing_for_today(conn, site_timezone, periods)
        detailed.extend(self._current_live_lines(
            conn,
            today_periods,
            site_timezone,
            int(today_revision["id"]) if today_revision else None,
        ))
        grouped, _ = self._with_meter_reconciliation(conn, detailed, site_timezone, periods)
        return apply_daily_export_tier(grouped)

    def _daily_site_totals(self, conn: sqlite3.Connection, bill_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Join authoritative local-day counters to the calculated daily bill.

        The six energy values come only from persisted ``daily_counters`` for
        enabled inverters.  Billing rows provide the money amount; they never
        manufacture an energy total when meter data is absent.
        """

        rows = fetch_all(
            conn,
            """SELECT dc.day,
                      COALESCE(SUM(dc.solar_kwh), 0.0) AS solar_kwh,
                      COALESCE(SUM(dc.load_kwh), 0.0) AS load_kwh,
                      COALESCE(SUM(dc.grid_import_kwh), 0.0) AS grid_import_kwh,
                      COALESCE(SUM(dc.grid_export_kwh), 0.0) AS grid_export_kwh,
                      CASE WHEN COUNT(dc.grid_import_peak_kwh) > 0 THEN SUM(dc.grid_import_peak_kwh) END AS grid_import_peak_kwh,
                      CASE WHEN COUNT(dc.grid_import_off_peak_kwh) > 0 THEN SUM(dc.grid_import_off_peak_kwh) END AS grid_import_off_peak_kwh,
                      CASE WHEN COUNT(dc.grid_export_peak_kwh) > 0 THEN SUM(dc.grid_export_peak_kwh) END AS grid_export_peak_kwh,
                      CASE WHEN COUNT(dc.grid_export_off_peak_kwh) > 0 THEN SUM(dc.grid_export_off_peak_kwh) END AS grid_export_off_peak_kwh,
                      COALESCE(SUM(dc.battery_charge_kwh), 0.0) AS battery_charge_kwh,
                      COALESCE(SUM(dc.battery_discharge_kwh), 0.0) AS battery_discharge_kwh
               FROM daily_counters dc
               JOIN inverter_profiles ip ON ip.id = dc.inverter_id
               WHERE ip.enabled = 1
               GROUP BY dc.day
               ORDER BY dc.day""",
        )
        amounts: dict[str, float] = {}
        plans: dict[str, dict[str, Any]] = {}
        for bill_row in bill_rows:
            amount = bill_row.get("amount_cents")
            if amount is not None:
                day = str(bill_row["day"])
                amounts[day] = amounts.get(day, 0.0) + float(amount)
            revision_id = bill_row.get("pricing_revision_id")
            if revision_id is not None:
                revision = fetch_one(
                    conn,
                    "SELECT plan_id, plan_name FROM billing_plan_revisions WHERE id=?",
                    (revision_id,),
                )
                if revision is not None:
                    plans.setdefault(str(bill_row["day"]), dict(revision))
        return [
            {
                "plan_id": plans.get(str(row["day"]), {}).get("plan_id"),
                "plan_name": plans.get(str(row["day"]), {}).get("plan_name"),
                **{key: round(float(row[key]), 6) for key in (
                    "solar_kwh", "load_kwh", "grid_import_kwh", "grid_export_kwh",
                    "battery_charge_kwh", "battery_discharge_kwh",
                )},
                **{
                    key: round(float(row[key]), 6) if row[key] is not None else None
                    for key in (
                        "grid_import_peak_kwh", "grid_import_off_peak_kwh",
                        "grid_export_peak_kwh", "grid_export_off_peak_kwh",
                    )
                },
                "day": row["day"],
                "daily_bill_amount_cents": round(amounts.get(row["day"], 0.0), 2),
            }
            for row in rows
        ]

    def dashboard_state(self) -> dict[str, Any]:
        """Build the dashboard payload consumed by the UI and MCP server.

        Live values are only shown when every enabled inverter is reachable
        (reachable flag set by the last poll). If any enabled inverter is
        unreachable, ``live`` is None and ``all_reachable`` is False so the UI
        can render "—" for every value with an unreachable message. No stale or
        simulated numbers are ever shown in that state.
        """

        settings = self.load_app_settings()
        with db_session(self.db_path) as conn:
            inverters = fetch_all(conn, "SELECT * FROM inverter_profiles ORDER BY id")
            plans = fetch_all(conn, "SELECT * FROM power_plans ORDER BY id")
            bill = self.current_bill_summary()
            live_rows = self.latest_live_state()

        # Determine reachability across all enabled inverters. An inverter is
        # considered unreachable if its flag says so, or if it has never
        # produced a telemetry row (no live data at all).
        enabled = [inv for inv in inverters if int(inv["enabled"])]
        rows_by_inverter = {row["inverter_id"]: row for row in live_rows}
        all_reachable = bool(enabled) and all(
            inv["id"] in rows_by_inverter and int(rows_by_inverter[inv["id"]]["reachable"]) == 1
            for inv in enabled
        )

        if not all_reachable:
            live = None
            totals = None
            live_observed_at = None
        else:
            totals = {
                "solar_kw": 0.0,
                "load_kw": 0.0,
                "grid_import_kw": 0.0,
                "grid_export_kw": 0.0,
                "battery_charge_kw": 0.0,
                "battery_discharge_kw": 0.0,
            }
            enabled_ids = {int(inverter["id"]) for inverter in enabled}
            required_rows = [rows_by_inverter[inverter_id] for inverter_id in enabled_ids]
            live_observed_at = min(
                datetime.fromisoformat(row["captured_at"]).isoformat() for row in required_rows
            )
            for row in live_rows:
                if int(row["inverter_id"]) not in enabled_ids or int(row["reachable"]) != 1:
                    continue
                for key in totals:
                    totals[key] += float(row[key])
            live = totals

            # Aggregate just the site's local-day counters. Direct device-day
            # registers are used where available; grid uses a persisted
            # lifetime/session counter delta. Raw lifetime totals remain
            # available for billing and completed-bucket rollups.
            totals = {
                "solar_total_kwh": 0.0,
                "load_total_kwh": 0.0,
                "grid_import_total_kwh": 0.0,
                "grid_export_total_kwh": 0.0,
                "battery_charge_total_kwh": 0.0,
                "battery_discharge_total_kwh": 0.0,
            }
            today = datetime.now(ZoneInfo(settings.site_timezone)).date().isoformat()
            with db_session(self.db_path) as conn:
                daily_totals = fetch_one(
                    conn,
                    """
                    SELECT
                        COALESCE(SUM(dc.solar_kwh), 0.0) AS solar_total_kwh,
                        COALESCE(SUM(dc.load_kwh), 0.0) AS load_total_kwh,
                        COALESCE(SUM(dc.grid_import_kwh), 0.0) AS grid_import_total_kwh,
                        COALESCE(SUM(dc.grid_export_kwh), 0.0) AS grid_export_total_kwh,
                        COALESCE(SUM(dc.battery_charge_kwh), 0.0) AS battery_charge_total_kwh,
                        COALESCE(SUM(dc.battery_discharge_kwh), 0.0) AS battery_discharge_total_kwh
                    FROM daily_counters dc
                    JOIN inverter_profiles ip ON ip.id = dc.inverter_id
                    WHERE dc.day = ? AND ip.enabled = 1 AND ip.reachable = 1
                    """,
                    (today,),
                )
            totals.update({key: round(float(daily_totals[key]), 6) for key in totals})

        return {
            "settings": settings.model_dump(),
            "inverters": inverters,
            "power_plans": plans,
            "live": live,
            "totals": totals,
            "live_observed_at": live_observed_at,
            "all_reachable": all_reachable,
            "bill": bill,
            "theme": settings.theme,
        }

    def chart_points(self, days: int = 14) -> list[dict[str, Any]]:
        """Generate chart points from the finalized billing daily totals.

        The overview and Billing page must show the same daily amount. Reusing
        ``current_bill_summary`` is important because it includes meter
        reconciliation, export tiering, and supply charges; summing raw
        rollup amounts here would omit those adjustments.
        """

        daily_totals = self.current_bill_summary().get("daily_site_totals", [])
        items = [
            (str(row["day"]), float(row["daily_bill_amount_cents"]))
            for row in daily_totals
            if row.get("daily_bill_amount_cents") is not None
        ][-days:]
        return [{"day": day, "amount_cents": round(amount, 2)} for day, amount in items]

    def weather_and_recommendation(self, inverter_id: int | None = None) -> dict[str, Any]:
        """Fetch weather and return a recommended inverter policy for AI mode."""

        # Look up the selected profile before any network request.  This makes
        # an unknown selected ID deterministically a 404, rather than allowing
        # an unrelated weather outage to mask that client error.
        with db_session(self.db_path) as conn:
            inverter = fetch_one(
                conn,
                "SELECT * FROM inverter_profiles WHERE id=?" if inverter_id is not None else "SELECT * FROM inverter_profiles ORDER BY id LIMIT 1",
                (inverter_id,) if inverter_id is not None else (),
            )
        if not inverter:
            identifier = inverter_id if inverter_id is not None else "available"
            raise KeyError(f"No inverter with id {identifier}")
        settings = self.load_app_settings()
        weather = fetch_open_meteo(settings.site_lat, settings.site_lon)
        reserve = int(inverter["battery_reserve_percent"])
        feed_in = float(inverter["battery_feed_in_limit_kw"])
        recommendation = recommend_battery_policy(weather, reserve, feed_in)
        return {"weather": asdict(weather), "recommendation": recommendation}

    def apply_recommendation(self, inverter_id: int, recommendation: dict[str, Any]) -> None:
        """Persist an AI recommendation back to the selected inverter profile."""

        with db_session(self.db_path) as conn:
            updated = conn.execute(
                """
                UPDATE inverter_profiles
                SET battery_reserve_percent = ?, battery_feed_in_limit_kw = ?
                WHERE id = ?
                """,
                (
                    int(recommendation["recommended_reserve_percent"]),
                    float(recommendation["recommended_feed_in_limit_kw"]),
                    inverter_id,
                ),
            )
            if not updated.rowcount:
                raise KeyError(f"No inverter with id {inverter_id}")

    @staticmethod
    def _empty_bill_summary(
        daily_site_totals: list[dict[str, Any]] | None = None,
        site_timezone: str = "Australia/Brisbane",
    ) -> dict[str, Any]:
        """Return the stable bill shape when no active plan can price data."""

        daily_site_totals = daily_site_totals or []
        today = datetime.now(ZoneInfo(site_timezone)).date().isoformat()
        today_totals = next((row for row in daily_site_totals if row["day"] == today), {})

        return {
            "plan": None, "total_cents": 0.0, "rows": [], "daily": [], "daily_site_totals": daily_site_totals,
            "today_grid_import_kwh": round(float(today_totals.get("grid_import_kwh", 0.0)), 4),
            "today_grid_export_kwh": round(float(today_totals.get("grid_export_kwh", 0.0)), 4),
            "supply_charge_cents": 0.0, "supply_charge_days": 0,
            "billing_window_applied": False, "lines": [], "rollups": [],
        }

    @staticmethod
    def _active_plan_id(conn: sqlite3.Connection) -> int | None:
        """Return the configured active plan ID, if it is validly encoded."""

        value = get_settings(conn).get("active_plan_id")
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _schedule_billing_revision(
        self,
        conn: sqlite3.Connection,
        plan_id: int,
        *,
        site_timezone: str | None = None,
    ) -> int | None:
        """Schedule the edited active pricing snapshot for the next local day."""

        if site_timezone is None:
            settings = get_settings(conn)
            site_timezone = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
        effective_from = (
            datetime.now(ZoneInfo(site_timezone)).date() + timedelta(days=1)
        ).isoformat()
        return create_billing_plan_revision(conn, plan_id, effective_from)

    @staticmethod
    def _revision_periods(revision: dict[str, Any] | sqlite3.Row | None) -> list[dict[str, Any]]:
        """Decode a persisted revision's TOU snapshot without exposing JSON to callers."""

        if revision is None:
            return []
        try:
            periods = json.loads(revision["tou_periods_json"])
        except (KeyError, TypeError, json.JSONDecodeError):
            return []
        return periods if isinstance(periods, list) else []

    def _revision_by_id(self, conn: sqlite3.Connection, revision_id: int | None) -> dict[str, Any] | None:
        if revision_id is None:
            return None
        return fetch_one(conn, "SELECT * FROM billing_plan_revisions WHERE id=?", (revision_id,))

    def _revision_for_day(self, conn: sqlite3.Connection, day: date) -> dict[str, Any] | None:
        """Resolve the immutable snapshot for the configured active plan/day."""

        plan_id = self._active_plan_id(conn)
        if plan_id is None:
            return None
        return fetch_one(
            conn,
            """SELECT * FROM billing_plan_revisions
               WHERE plan_id=? AND effective_from <= ?
               ORDER BY effective_from DESC, id DESC LIMIT 1""",
            (plan_id, day.isoformat()),
        )

    def _pricing_for_rollup(
        self,
        conn: sqlite3.Connection,
        row: dict[str, Any],
        site_timezone: str,
        fallback_periods: list[dict[str, Any]],
    ) -> tuple[int | None, list[dict[str, Any]]]:
        """Get the rollup's immutable pricing, with a legacy read fallback."""

        revision = self._revision_by_id(conn, row.get("pricing_revision_id"))
        if revision is None:
            revision = self._revision_for_day(
                conn, self._local_day(row["bucket_start"], ZoneInfo(site_timezone))
            )
        periods = self._revision_periods(revision) or fallback_periods
        return (int(revision["id"]) if revision is not None else None), periods

    def _pricing_for_today(
        self,
        conn: sqlite3.Connection,
        site_timezone: str,
        fallback_periods: list[dict[str, Any]],
        fallback_supply_charge: float = 0.0,
    ) -> tuple[int | None, list[dict[str, Any]], float]:
        revision = self._revision_for_day(conn, datetime.now(ZoneInfo(site_timezone)).date())
        periods = self._revision_periods(revision) or fallback_periods
        charge = float(
            (revision["daily_supply_charge_cents"] if revision is not None else fallback_supply_charge) or 0.0
        )
        return (int(revision["id"]) if revision is not None else None, periods, charge)

    def _rollup_bill_lines(
        self,
        conn: sqlite3.Connection,
        rollups: list[dict[str, Any]],
        site_timezone: str,
        fallback_periods: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Price each completed rollup using its stored revision identity."""

        detailed: list[dict[str, Any]] = []
        for row in rollups:
            revision_id, periods = self._pricing_for_rollup(
                conn, row, site_timezone, fallback_periods
            )
            if not periods:
                continue
            lines = aggregate_bill_lines(
                [{
                    "captured_at": datetime.fromisoformat(row["bucket_start"]),
                    "grid_import_kwh": row["grid_import_kwh"],
                    "grid_export_kwh": row["grid_export_kwh"],
                    "pricing_revision_id": revision_id,
                }],
                periods,
                site_timezone,
            )
            detailed.extend(lines)
        return detailed

    def _supply_charge_for_day(
        self,
        conn: sqlite3.Connection,
        day: str,
        bill_rows: list[dict[str, Any]],
        site_timezone: str,
        fallback_supply_charge: float,
    ) -> tuple[int | None, float]:
        """Resolve the fixed charge from the same revision as that day's energy."""

        revision_id = next(
            (
                int(row["pricing_revision_id"])
                for row in bill_rows
                if row.get("day") == day and row.get("pricing_revision_id") is not None
            ),
            None,
        )
        revision = self._revision_by_id(conn, revision_id)
        if revision is None:
            revision = self._revision_for_day(
                conn, date.fromisoformat(day)
            )
        if revision is None:
            return None, fallback_supply_charge
        return int(revision["id"]), float(revision["daily_supply_charge_cents"] or 0.0)

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _row_to_reading(self, row: dict[str, Any]) -> InverterReading:
        return InverterReading(
            captured_at=datetime.fromisoformat(row["captured_at"]),
            solar_kw=float(row["solar_kw"]),
            load_kw=float(row["load_kw"]),
            grid_import_kw=float(row["grid_import_kw"]),
            grid_export_kw=float(row["grid_export_kw"]),
            battery_charge_kw=float(row["battery_charge_kw"]),
            battery_discharge_kw=float(row["battery_discharge_kw"]),
            solar_total_kwh=float(row["solar_total_kwh"]),
            load_total_kwh=float(row["load_total_kwh"]),
            grid_import_total_kwh=float(row["grid_import_total_kwh"]),
            grid_export_total_kwh=float(row["grid_export_total_kwh"]),
            battery_charge_total_kwh=float(row["battery_charge_total_kwh"]),
            battery_discharge_total_kwh=float(row["battery_discharge_total_kwh"]),
            lifetime=bool(row.get("lifetime", 0)),
        )

    def _reading_to_dict(self, reading: InverterReading, deltas: dict[str, float]) -> dict[str, Any]:
        data = {
            "captured_at": reading.captured_at.isoformat(),
            "solar_kw": reading.solar_kw,
            "load_kw": reading.load_kw,
            "grid_import_kw": reading.grid_import_kw,
            "grid_export_kw": reading.grid_export_kw,
            "battery_charge_kw": reading.battery_charge_kw,
            "battery_discharge_kw": reading.battery_discharge_kw,
            "solar_total_kwh": reading.solar_total_kwh,
            "load_total_kwh": reading.load_total_kwh,
            "grid_import_total_kwh": reading.grid_import_total_kwh,
            "grid_export_total_kwh": reading.grid_export_total_kwh,
            "battery_charge_total_kwh": reading.battery_charge_total_kwh,
            "battery_discharge_total_kwh": reading.battery_discharge_total_kwh,
            "lifetime": reading.lifetime,
        }
        data.update(deltas)
        return data

    @staticmethod
    def _valid_timezone(value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            return "Australia/Brisbane"
        return value

    @staticmethod
    def _deltas_for_reading(reading: InverterReading, previous: InverterReading | None) -> dict[str, float]:
        """Return zeroes for the one row where a totals source changes."""

        if previous and reading.lifetime != previous.lifetime:
            return reading.deltas_from(None)
        return reading.deltas_from(previous)

    def _update_daily_counters(self, conn: sqlite3.Connection, inverter_id: int, reading: InverterReading) -> None:
        """Maintain local-day totals without dropping energy at a source reset.

        ``*_baseline_kwh`` is the last observed value for that metric, not a
        permanently fixed midnight baseline. This lets one row retain its
        accumulated day value while a lifetime/session counter resets or the
        adapter moves between a device daily register and a lifetime register.
        A first lifetime/session sample cannot reconstruct energy before the
        app saw it, whereas a direct device daily register can.
        """

        settings = get_settings(conn)
        timezone_name = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
        day = reading.captured_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        site_zone = ZoneInfo(timezone_name)
        day_start = datetime.combine(
            reading.captured_at.astimezone(site_zone).date(), time.min, tzinfo=site_zone,
        ).astimezone(timezone.utc)
        observations = self._daily_observations(reading)
        existing = fetch_one(conn, "SELECT * FROM daily_counters WHERE inverter_id = ? AND day = ?", (inverter_id, day))
        if not existing:
            columns = ", ".join(
                [*(f"{key}_baseline_kwh" for key in self._DAILY_COUNTER_KEYS),
                 *(f"{key}_source" for key in self._DAILY_COUNTER_KEYS)]
            )
            initial_totals = self._initial_daily_values(
                conn,
                inverter_id,
                day_start,
                observations,
                max_baseline_age=timedelta(
                    seconds=max(60, int(settings.get("poll_interval_seconds", 30)) * 2),
                ),
            )
            conn.execute(
                f"""INSERT INTO daily_counters
                    (inverter_id, day, solar_kwh, load_kwh, grid_import_kwh,
                     grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, {columns})
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inverter_id, day, *initial_totals,
                    *(value for _, value in observations.values()),
                    *(source for source, _ in observations.values()),
                ),
            )
            return

        values: dict[str, float] = {}
        baselines: dict[str, float] = {}
        sources: dict[str, str] = {}
        for key in self._DAILY_COUNTER_KEYS:
            source, value = observations[key]
            old_total = float(existing[f"{key}_kwh"])
            old_source = str(existing.get(f"{key}_source", "legacy"))
            old_baseline = float(existing[f"{key}_baseline_kwh"])
            if old_source == source:
                # Counter resets and session restarts contribute no negative
                # energy; subsequent increasing samples continue from the
                # retained daily value.
                total = old_total + max(0.0, value - old_baseline)
            elif source == "daily":
                # A device day register is already the local-day value. It can
                # recover energy that was missed before a lifetime baseline
                # existed, but never lowers a value we have already observed.
                total = max(old_total, max(0.0, value))
            elif old_source == "legacy" and source == "lifetime":
                # Pre-source-tracking versions stored a valid midnight
                # lifetime baseline. Preserve it during migration rather than
                # treating the next successful poll as a new baseline.
                total = max(old_total, max(0.0, value - old_baseline))
            else:
                # The first sample from a new lifetime/session source is only
                # a baseline; adding it would fabricate a large delta.
                total = old_total
            values[key] = round(total, 6)
            baselines[key] = value
            sources[key] = source
        conn.execute(
            """UPDATE daily_counters SET solar_kwh=?, load_kwh=?, grid_import_kwh=?, grid_export_kwh=?,
               battery_charge_kwh=?, battery_discharge_kwh=?,
               solar_baseline_kwh=?, load_baseline_kwh=?, grid_import_baseline_kwh=?, grid_export_baseline_kwh=?,
               battery_charge_baseline_kwh=?, battery_discharge_baseline_kwh=?,
               solar_source=?, load_source=?, grid_import_source=?, grid_export_source=?,
               battery_charge_source=?, battery_discharge_source=?
               WHERE inverter_id=? AND day=?""",
            (*values.values(), *baselines.values(), *sources.values(), inverter_id, day),
        )

    def _daily_observations(self, reading: InverterReading) -> dict[str, tuple[str, float]]:
        """Choose the most authoritative source for each dashboard metric.

        Grid deliberately has no unit-1 daily source.  Although the inverter
        exposes 30554/30560 as daily import/export registers, they measure the
        inverter AC terminal rather than the plant grid sensor on this site
        (30554 tracked load plus grid export in the live probe). The adapter
        prefers plant-unit lifetime registers 30260/30264 and
        falls back per counter to 30216/30220 for older register maps. Both
        are grid-meter sources and need a local midnight baseline.
        """

        return {
            key: (
                "daily" if key in reading.daily_totals_kwh else ("lifetime" if reading.lifetime else "session"),
                float(reading.daily_totals_kwh[key])
                if key in reading.daily_totals_kwh
                else float(getattr(reading, self._TOTAL_COLUMNS[key])),
            )
            for key in self._DAILY_COUNTER_KEYS
        }

    def _initial_daily_values(
        self,
        conn: sqlite3.Connection,
        inverter_id: int,
        day_start: datetime,
        observations: dict[str, tuple[str, float]],
        *,
        max_baseline_age: timedelta,
    ) -> list[float]:
        """Seed a new local-day row without inventing pre-baseline energy.

        A direct device-day register can report the entire local day on its
        first poll. For lifetime counters, recover the local-day delta only if
        the app has a persisted sample within the polling window before local
        midnight; otherwise the first observation is a baseline and earlier
        energy is unknowable until the next local day.
        """

        prior = fetch_one(
            conn,
            """SELECT * FROM telemetry_raw
               WHERE inverter_id = ? AND lifetime = 1 AND captured_at <= ?
               ORDER BY captured_at DESC, id DESC LIMIT 1""",
            (inverter_id, day_start.isoformat()),
        )
        prior_is_near_midnight = False
        if prior:
            prior_captured = datetime.fromisoformat(prior["captured_at"])
            if prior_captured.tzinfo is None:
                prior_captured = prior_captured.replace(tzinfo=timezone.utc)
            prior_is_near_midnight = (
                timedelta(0)
                <= day_start - prior_captured.astimezone(timezone.utc)
                <= max_baseline_age
            )

        values: list[float] = []
        for key in self._DAILY_COUNTER_KEYS:
            source, value = observations[key]
            if source == "daily":
                values.append(max(0.0, value))
            elif source == "lifetime" and prior and prior_is_near_midnight:
                # A negative delta is a reset/rollover/source replacement;
                # retain it as a fresh baseline rather than fabricating kWh.
                values.append(max(0.0, value - float(prior[self._TOTAL_COLUMNS[key]])))
            else:
                values.append(0.0)
        return values

    @staticmethod
    def _local_day(value: str, site_zone: ZoneInfo) -> date:
        """Return an ISO timestamp's site-local date."""

        captured = datetime.fromisoformat(value)
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        return captured.astimezone(site_zone).date()

    def _daily_counter_total(self, conn: sqlite3.Connection, day: str, column: str) -> float:
        """Return an enabled-inverter daily counter total for one local day."""

        if column not in {"solar_kwh", "grid_import_kwh", "grid_export_kwh"}:
            raise ValueError(f"Unsupported daily counter column: {column}")
        row = fetch_one(
            conn,
            f"""SELECT COALESCE(SUM(dc.{column}), 0.0) AS total
               FROM daily_counters dc
               JOIN inverter_profiles ip ON ip.id = dc.inverter_id
               WHERE dc.day = ? AND ip.enabled = 1""",
            (day,),
        ) or {}
        return float(row.get("total", 0.0))

    def _metered_day_totals(self, conn: sqlite3.Connection, day: str) -> dict[str, float]:
        """Return the enabled plant-meter totals for one local day."""

        row = fetch_one(
            conn,
            """SELECT COALESCE(SUM(dc.grid_import_kwh), 0.0) AS grid_import_kwh,
                      COALESCE(SUM(dc.grid_export_kwh), 0.0) AS grid_export_kwh
               FROM daily_counters dc
               JOIN inverter_profiles ip ON ip.id = dc.inverter_id
               WHERE dc.day = ? AND ip.enabled = 1""",
            (day,),
        ) or {}
        return {
            "grid_import_kwh": float(row.get("grid_import_kwh", 0.0)),
            "grid_export_kwh": float(row.get("grid_export_kwh", 0.0)),
        }

    def _flat_day_period(self, periods: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
        """Return the rate when a direction has exactly one full-day period.

        A single 0-1440 period means the tariff is flat for that direction, so
        a meter-reconciliation adjustment can be priced safely.  Any TOU split
        (multiple periods, or a partial-day period) returns None so the
        adjustment stays explicitly unpriced rather than assigned to a guessed
        tariff.
        """

        matching = [p for p in periods if p["direction"] == direction]
        if len(matching) != 1:
            return None
        period = matching[0]
        if int(period["start_minute"]) != 0 or int(period["end_minute"]) != 1440:
            return None
        return period

    def _with_meter_reconciliation(
        self,
        conn: sqlite3.Connection,
        detailed: list[dict[str, Any]],
        site_timezone: str,
        periods: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """Keep billed daily kWh equal to plant-meter totals without fake TOU.

        A source restart after local midnight can make the pre-restart interval
        unknowable.  The daily meter total is still real, but its TOU split is
        not.  Emit an adjustment for that difference; price it only when the
        direction has a single flat full-day tariff, otherwise leave it
        explicitly unpriced rather than assigning it to a guessed period.
        """

        periods = periods or []
        grouped = rollup_by_day_and_period(detailed, site_timezone)
        today = datetime.now(ZoneInfo(site_timezone)).date()
        meter_rows = fetch_all(
            conn,
            """SELECT dc.day,
                      COALESCE(SUM(dc.grid_import_kwh), 0.0) AS grid_import_kwh,
                      COALESCE(SUM(dc.grid_export_kwh), 0.0) AS grid_export_kwh
               FROM daily_counters dc
               JOIN inverter_profiles ip ON ip.id = dc.inverter_id
               WHERE ip.enabled = 1
               GROUP BY dc.day""",
        )
        metered_by_day = {
            row["day"]: {
                "grid_import_kwh": float(row["grid_import_kwh"]),
                "grid_export_kwh": float(row["grid_export_kwh"]),
            }
            for row in meter_rows
        }
        for day, metered in metered_by_day.items():
            for direction, column in (("import", "grid_import_kwh"), ("export", "grid_export_kwh")):
                observed = sum(
                    float(row["kwh"])
                    for row in grouped
                    if row["day"] == day and row["direction"] == direction
                )
                difference = metered[column] - observed
                if difference < -0.000001:
                    # A stale/session rollup can be larger than the meter total.
                    # Drop its invented tariff allocation rather than using a
                    # negative kWh adjustment at an arbitrary tariff.
                    grouped = [
                        row for row in grouped
                        if not (row["day"] == day and row["direction"] == direction)
                    ]
                    difference = metered[column]
                if difference > 0.000001:
                    revision_id = next(
                        (
                            int(row["pricing_revision_id"])
                            for row in grouped
                            if row["day"] == day and row.get("pricing_revision_id") is not None
                        ),
                        None,
                    )
                    revision = self._revision_by_id(conn, revision_id)
                    if revision is None:
                        revision = self._revision_for_day(conn, date.fromisoformat(day))
                    day_periods = self._revision_periods(revision) or periods
                    if revision is not None:
                        revision_id = int(revision["id"])
                    flat_period = self._flat_day_period(day_periods, direction)
                    if flat_period is not None:
                        flat_rate = float(flat_period["rate_cents_per_kwh"])
                        # A single flat full-day tariff makes the adjustment's
                        # price unambiguous, so bill it at that rate.  Export is
                        # a credit (negative amount), matching aggregate_bill_lines.
                        amount = difference * flat_rate
                        if direction == "export":
                            amount *= -1.0
                        grouped.append({
                            "day": day,
                            "period_label": f"Meter reconciliation (flat {flat_rate:g}c)",
                            "direction": direction,
                            "kwh": round(difference, 4),
                            "rate_cents_per_kwh": flat_rate,
                            "amount_cents": round(amount, 3),
                            "tou_period_id": flat_period.get("id"),
                            "pricing_revision_id": revision_id,
                            "export_tier_kwh": float(flat_period.get("export_tier_kwh", 0.0) or 0.0),
                            "export_tier_rate_cents_per_kwh": float(flat_period.get("export_tier_rate_cents_per_kwh", 0.0) or 0.0),
                            "export_excess_rate_cents_per_kwh": float(flat_period.get("export_excess_rate_cents_per_kwh", 0.0) or 0.0),
                        })
                    else:
                        grouped.append({
                            "day": day,
                            "period_label": "Meter reconciliation (TOU unavailable)",
                            "direction": direction,
                            "kwh": round(difference, 4),
                            "rate_cents_per_kwh": None,
                            "amount_cents": None,
                            "unpriced": True,
                            "pricing_revision_id": revision_id,
                        })
        return grouped, metered_by_day.get(today.isoformat(), {
            "grid_import_kwh": 0.0,
            "grid_export_kwh": 0.0,
        })

    def _current_live_lines(
        self,
        conn: sqlite3.Connection,
        periods: list[dict[str, Any]],
        site_timezone: str,
        pricing_revision_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Price the open half-hour from lifetime counter deltas, when available."""

        start = bucket_start(datetime.now(timezone.utc))
        rows = fetch_all(conn, "SELECT DISTINCT inverter_id FROM telemetry_raw")
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            inverter_id = row["inverter_id"]
            baseline = fetch_one(conn, "SELECT * FROM telemetry_raw WHERE inverter_id=? AND captured_at >= ? AND lifetime=1 ORDER BY captured_at ASC, id ASC LIMIT 1", (inverter_id, start.isoformat()))
            current = fetch_one(conn, "SELECT * FROM telemetry_raw WHERE inverter_id=? AND lifetime=1 ORDER BY captured_at DESC, id DESC LIMIT 1", (inverter_id,))
            if not baseline or not current:
                continue
            snapshots.append({
                "captured_at": start,
                "grid_import_kwh": max(0.0, float(current["grid_import_total_kwh"]) - float(baseline["grid_import_total_kwh"])),
                "grid_export_kwh": max(0.0, float(current["grid_export_total_kwh"]) - float(baseline["grid_export_total_kwh"])),
                "pricing_revision_id": pricing_revision_id,
            })
        lines = aggregate_bill_lines(snapshots, periods, site_timezone)
        for line in lines:
            line["period_label"] = f"Current (live) — {line['period_label']}"
        return lines

    def _match_period(self, periods: list[dict[str, Any]], captured: datetime, direction: str, site_timezone: str) -> dict[str, Any] | None:
        return find_period(periods, captured, direction, site_timezone)
