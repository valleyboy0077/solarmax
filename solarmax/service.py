"""Application service layer for dashboard, polling, and updates."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .billing import aggregate_bill_lines, bucket_start, find_period, rollup_by_day_and_period
from .db import db_session, fetch_all, fetch_one, get_settings, init_db, set_setting
from .inverters.base import InverterReading
from .inverters.registry import get_adapter
from .models import AppSettings, InverterProfile, PowerPlan, TouPeriod
from .weather import WeatherSummary, fetch_open_meteo, recommend_battery_policy


class SolarmaxService:
    """Coordinates persistence, polling, billing, and AI helpers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        init_db(db_path)

    # ---------------------------------------------------------------------
    # Settings and configuration
    # ---------------------------------------------------------------------

    def load_app_settings(self) -> AppSettings:
        with db_session(self.db_path) as conn:
            raw = get_settings(conn)
        return AppSettings(
            theme=raw.get("theme", "classic-dark"),
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
        settings.update({k: v for k, v in payload.items() if v is not None})
        with db_session(self.db_path) as conn:
            settings["site_timezone"] = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            for key in ("theme", "mode", "site_name", "site_lat", "site_lon", "site_timezone", "poll_interval_seconds"):
                if key in settings:
                    set_setting(conn, key, str(settings[key]))
            set_setting(conn, "active_plan_id", "" if settings.get("active_plan_id") is None else str(settings["active_plan_id"]))

    def list_inverters(self) -> list[dict[str, Any]]:
        with db_session(self.db_path) as conn:
            return fetch_all(conn, "SELECT * FROM inverter_profiles ORDER BY id")

    def get_inverter(self, inverter_id: int) -> dict[str, Any] | None:
        with db_session(self.db_path) as conn:
            return fetch_one(conn, "SELECT * FROM inverter_profiles WHERE id = ?", (inverter_id,))

    def upsert_inverter(self, payload: dict[str, Any]) -> int:
        profile = InverterProfile(**payload)
        with db_session(self.db_path) as conn:
            if profile.id:
                conn.execute(
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
                conn.execute(
                    """
                    UPDATE power_plans
                    SET provider_name=?, plan_name=?, billing_cycle=?, billing_start_day=?, billing_start_month=?, daily_supply_charge_cents=?, notes=?
                    WHERE id=?
                    """,
                    (
                        plan.provider_name,
                        plan.plan_name,
                        plan.billing_cycle,
                        plan.billing_start_day,
                        plan.billing_start_month,
                        plan.daily_supply_charge_cents,
                        plan.notes,
                        plan.id,
                    ),
                )
                return plan.id
            cur = conn.execute(
                """
                INSERT INTO power_plans
                (provider_name, plan_name, billing_cycle, billing_start_day, billing_start_month, daily_supply_charge_cents, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.provider_name,
                    plan.plan_name,
                    plan.billing_cycle,
                    plan.billing_start_day,
                    plan.billing_start_month,
                    plan.daily_supply_charge_cents,
                    plan.notes,
                ),
            )
            return int(cur.lastrowid)

    def update_daily_supply_charge(self, plan_id: int, cents: float) -> None:
        """Update only the fixed daily charge for an existing plan."""
        validated = PowerPlan(provider_name="Existing", plan_name="Existing", daily_supply_charge_cents=cents)
        with db_session(self.db_path) as conn:
            conn.execute("UPDATE power_plans SET daily_supply_charge_cents=? WHERE id=?", (validated.daily_supply_charge_cents, plan_id))

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
                INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (plan_id, period.direction, period.label, period.start_minute, period.end_minute, period.rate_cents_per_kwh)
                    for period in validated
                ],
            )

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
            rows = fetch_all(conn, "SELECT * FROM telemetry_raw ORDER BY captured_at")
            by_bucket: dict[tuple[int, str], list[dict[str, Any]]] = {}
            for row in rows:
                captured = datetime.fromisoformat(row["captured_at"])
                start = bucket_start(captured).isoformat()
                by_bucket.setdefault((row["inverter_id"], start), []).append(row)
            active_cutoff = bucket_start(datetime.now(timezone.utc))
            for (inverter_id, start_iso), bucket_rows in by_bucket.items():
                start = datetime.fromisoformat(start_iso)
                end = start + timedelta(minutes=30)
                if end > active_cutoff:
                    continue
                sums = {k: 0.0 for k in ("delta_solar_kwh", "delta_load_kwh", "delta_grid_import_kwh", "delta_grid_export_kwh", "delta_battery_charge_kwh", "delta_battery_discharge_kwh")}
                for row in bucket_rows:
                    for key in sums:
                        sums[key] += float(row[key])
                conn.execute(
                    """
                    INSERT INTO telemetry_rollups
                    (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh, grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh, amount_cents)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(inverter_id, bucket_start) DO UPDATE SET
                        bucket_end=excluded.bucket_end,
                        solar_kwh=excluded.solar_kwh,
                        load_kwh=excluded.load_kwh,
                        grid_import_kwh=excluded.grid_import_kwh,
                        grid_export_kwh=excluded.grid_export_kwh,
                        battery_charge_kwh=excluded.battery_charge_kwh,
                        battery_discharge_kwh=excluded.battery_discharge_kwh,
                        amount_cents=excluded.amount_cents
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
                    ),
                )
        self.recalculate_rollup_amounts()

    def recalculate_rollup_amounts(self) -> None:
        """Re-price rollups using the active plan and its TOU brackets."""

        with db_session(self.db_path) as conn:
            settings = get_settings(conn)
            plan_id = int(settings["active_plan_id"]) if settings.get("active_plan_id") else None
            if not plan_id:
                return
            periods = fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
            site_timezone = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            rollups = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
            for row in rollups:
                captured = datetime.fromisoformat(row["bucket_start"])
                import_period = self._match_period(periods, captured, "import", site_timezone)
                export_period = self._match_period(periods, captured, "export", site_timezone)
                amount = 0.0
                if import_period:
                    amount += float(row["grid_import_kwh"]) * float(import_period["rate_cents_per_kwh"])
                if export_period:
                    amount -= float(row["grid_export_kwh"]) * float(export_period["rate_cents_per_kwh"])
                conn.execute("UPDATE telemetry_rollups SET amount_cents = ? WHERE id = ?", (amount, row["id"]))

    def current_bill_summary(self) -> dict[str, Any]:
        """Compute the current billing summary and line breakdown."""

        with db_session(self.db_path) as conn:
            settings = get_settings(conn)
            plan_id = int(settings["active_plan_id"]) if settings.get("active_plan_id") else None
            if not plan_id:
                return {"total_cents": 0.0, "lines": [], "rollups": []}
            plan = fetch_one(conn, "SELECT * FROM power_plans WHERE id = ?", (plan_id,))
            periods = fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
            site_timezone = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
            rollups = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
            detailed = aggregate_bill_lines(
                [
                    {
                        "captured_at": datetime.fromisoformat(row["bucket_start"]),
                        "grid_import_kwh": row["grid_import_kwh"],
                        "grid_export_kwh": row["grid_export_kwh"],
                    }
                    for row in rollups
                ],
                periods,
                site_timezone,
            )
            detailed.extend(self._current_live_lines(conn, periods, site_timezone))
            # Rollups can contain stale/duplicate export rows for the open
            # local day. The lifetime-backed daily counter is authoritative for
            # today's total, so replace today's export lines with one value.
            today = datetime.now(ZoneInfo(site_timezone)).date()
            today_export = sum(
                float(row["grid_export_kwh"])
                for row in fetch_all(
                    conn,
                    "SELECT grid_export_kwh FROM daily_counters WHERE day = ?",
                    (today.isoformat(),),
                )
            )
            detailed = [
                line for line in detailed
                if not (line["direction"] == "export" and line["day"] == today)
            ]
            export_period = find_period(periods, datetime.now(timezone.utc), "export", site_timezone)
            if export_period:
                detailed.append({
                    "day": today,
                    "captured_at": datetime.now(timezone.utc),
                    "period_label": "Grid export today",
                    "direction": "export",
                    "kwh": today_export,
                    "rate_cents_per_kwh": float(export_period["rate_cents_per_kwh"]),
                    "amount_cents": -today_export * float(export_period["rate_cents_per_kwh"]),
                })
            grouped = rollup_by_day_and_period(detailed, site_timezone)
            supply_charge_cents = float(plan.get("daily_supply_charge_cents", 0.0) or 0.0)
            supply_days = sorted({row["day"] for row in grouped})
            grouped.extend({
                "day": day,
                "period_label": "Daily supply charge",
                "direction": "fixed",
                "kwh": 0.0,
                "rate_cents_per_kwh": 0.0,
                "amount_cents": round(supply_charge_cents, 3),
            } for day in supply_days)
            total = sum(float(row["amount_cents"]) for row in grouped)
            daily = self.daily_bill_breakdown(conn, plan_id, site_timezone)
            return {
                "plan": plan,
                "total_cents": round(total, 2),
                "rows": grouped,
                "daily": daily,
                "today_grid_export_kwh": round(today_export, 4),
                "supply_charge_cents": supply_charge_cents,
                "supply_charge_days": len(supply_days),
            }

    def daily_bill_breakdown(self, conn: sqlite3.Connection, plan_id: int, site_timezone: str | None = None) -> list[dict[str, Any]]:
        """Return the per-day/per-period breakdown for the current bill screen."""

        periods = fetch_all(conn, "SELECT * FROM tou_periods WHERE plan_id = ?", (plan_id,))
        site_timezone = site_timezone or self.load_app_settings().site_timezone
        rollups = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
        detailed = aggregate_bill_lines(
            [
                {
                    "captured_at": datetime.fromisoformat(row["bucket_start"]),
                    "grid_import_kwh": row["grid_import_kwh"],
                    "grid_export_kwh": row["grid_export_kwh"],
                }
                for row in rollups
            ],
            periods,
            site_timezone,
        )
        detailed.extend(self._current_live_lines(conn, periods, site_timezone))
        return rollup_by_day_and_period(detailed, site_timezone)

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
            int(row["reachable"]) == 1 for row in rows_by_inverter.values()
        ) and all(inv["id"] in rows_by_inverter for inv in enabled)

        if not all_reachable:
            live = None
        else:
            totals = {
                "solar_kw": 0.0,
                "load_kw": 0.0,
                "grid_import_kw": 0.0,
                "grid_export_kw": 0.0,
                "battery_charge_kw": 0.0,
                "battery_discharge_kw": 0.0,
            }
            for row in live_rows:
                if int(row["reachable"]) != 1:
                    continue
                for key in totals:
                    totals[key] += float(row[key])
            live = totals

        return {
            "settings": settings.model_dump(),
            "inverters": inverters,
            "power_plans": plans,
            "live": live,
            "all_reachable": all_reachable,
            "bill": bill,
            "theme": settings.theme,
        }

    def chart_points(self, days: int = 14) -> list[dict[str, Any]]:
        """Generate daily net billing bars for the dashboard chart."""

        with db_session(self.db_path) as conn:
            site_timezone = self._valid_timezone(get_settings(conn).get("site_timezone", "Australia/Brisbane"))
            rows = fetch_all(conn, "SELECT * FROM telemetry_rollups ORDER BY bucket_start")
            settings = get_settings(conn)
            plan_id = int(settings["active_plan_id"]) if settings.get("active_plan_id") else None
            supply_charge_cents = 0.0
            if plan_id:
                plan = fetch_one(conn, "SELECT daily_supply_charge_cents FROM power_plans WHERE id = ?", (plan_id,))
                supply_charge_cents = float((plan or {}).get("daily_supply_charge_cents", 0.0) or 0.0)
            grouped: dict[str, float] = {}
            for row in rows:
                day = datetime.fromisoformat(row["bucket_start"]).astimezone(ZoneInfo(site_timezone)).date().isoformat()
                grouped[day] = grouped.get(day, 0.0) + float(row["amount_cents"])
            for day in grouped:
                grouped[day] += supply_charge_cents
        items = sorted(grouped.items())[-days:]
        return [{"day": day, "amount_cents": round(amount, 2)} for day, amount in items]

    def weather_and_recommendation(self) -> dict[str, Any]:
        """Fetch weather and return a recommended inverter policy for AI mode."""

        settings = self.load_app_settings()
        weather = fetch_open_meteo(settings.site_lat, settings.site_lon)
        with db_session(self.db_path) as conn:
            inverter = fetch_one(conn, "SELECT * FROM inverter_profiles ORDER BY id LIMIT 1")
        reserve = int(inverter["battery_reserve_percent"]) if inverter else 20
        feed_in = float(inverter["battery_feed_in_limit_kw"]) if inverter else 0.0
        recommendation = recommend_battery_policy(weather, reserve, feed_in)
        return {"weather": asdict(weather), "recommendation": recommendation}

    def apply_recommendation(self, inverter_id: int, recommendation: dict[str, Any]) -> None:
        """Persist an AI recommendation back to the selected inverter profile."""

        with db_session(self.db_path) as conn:
            conn.execute(
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
        """Maintain daily lifetime-counter deltas from the site's midnight baseline."""

        if not reading.lifetime:
            return
        settings = get_settings(conn)
        timezone_name = self._valid_timezone(settings.get("site_timezone", "Australia/Brisbane"))
        day = reading.captured_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        totals = {
            "solar": reading.solar_total_kwh, "load": reading.load_total_kwh,
            "grid_import": reading.grid_import_total_kwh, "grid_export": reading.grid_export_total_kwh,
            "battery_charge": reading.battery_charge_total_kwh, "battery_discharge": reading.battery_discharge_total_kwh,
        }
        existing = fetch_one(conn, "SELECT * FROM daily_counters WHERE inverter_id = ? AND day = ?", (inverter_id, day))
        if not existing:
            columns = ", ".join(f"{key}_baseline_kwh" for key in totals)
            conn.execute(
                f"INSERT INTO daily_counters (inverter_id, day, {columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (inverter_id, day, *totals.values()),
            )
            return
        values = {key: max(0.0, value - float(existing[f"{key}_baseline_kwh"])) for key, value in totals.items()}
        conn.execute(
            """UPDATE daily_counters SET solar_kwh=?, load_kwh=?, grid_import_kwh=?, grid_export_kwh=?,
               battery_charge_kwh=?, battery_discharge_kwh=? WHERE inverter_id=? AND day=?""",
            (*values.values(), inverter_id, day),
        )

    def _current_live_lines(self, conn: sqlite3.Connection, periods: list[dict[str, Any]], site_timezone: str) -> list[dict[str, Any]]:
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
            })
        lines = aggregate_bill_lines(snapshots, periods, site_timezone)
        for line in lines:
            line["period_label"] = f"Current (live) — {line['period_label']}"
        return lines

    def _match_period(self, periods: list[dict[str, Any]], captured: datetime, direction: str, site_timezone: str) -> dict[str, Any] | None:
        return find_period(periods, captured, direction, site_timezone)
