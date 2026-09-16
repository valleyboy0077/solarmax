from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from solarmax.billing import aggregate_bill_lines, apply_daily_export_tier, find_period, rollup_by_day_and_period
from solarmax.inverters.base import InverterReading
from solarmax.inverters.modbus import ModbusError
from solarmax.inverters.sigenstor_ec_20_0_tp_au import SigenStorEC20TPAUAdapter
from solarmax.service import SolarmaxService
from solarmax.db import ORIGIN_IMPORT_PERIODS, db_session, init_db
from solarmax.main import _currency_to_cents
from solarmax.main import templates


PERIODS = [
    {"direction": "import", "label": "Off-peak", "start_minute": 0, "end_minute": 960, "rate_cents_per_kwh": 10},
    {"direction": "import", "label": "Peak", "start_minute": 960, "end_minute": 1260, "rate_cents_per_kwh": 47.78},
]


ORIGIN_PERIODS = [
    {"direction": "import", "label": label, "start_minute": start, "end_minute": end, "rate_cents_per_kwh": rate}
    for label, start, end, rate in ORIGIN_IMPORT_PERIODS
]


def reading(total: float, lifetime: bool) -> InverterReading:
    return InverterReading(
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc), solar_kw=0, load_kw=0,
        grid_import_kw=0, grid_export_kw=0, battery_charge_kw=0, battery_discharge_kw=0,
        solar_total_kwh=total, load_total_kwh=total, grid_import_total_kwh=total,
        grid_export_total_kwh=total, battery_charge_total_kwh=total,
        battery_discharge_total_kwh=total, lifetime=lifetime,
    )


def test_find_period_uses_aest_wall_clock():
    # 07:00 UTC is 17:00 AEST, so this must be peak rather than off-peak.
    when = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
    assert find_period(PERIODS, when, "import", "Australia/Brisbane")["label"] == "Peak"


def test_origin_import_period_boundaries_are_exact():
    for hour, minute, label, rate in (
        (0, 0, "Shoulder", 25.30),
        (8, 59, "Shoulder", 25.30),
        (9, 0, "Off-peak", 6.98),
        (15, 59, "Off-peak", 6.98),
        (16, 0, "Peak", 47.78),
        (20, 59, "Peak", 47.78),
        (21, 0, "Shoulder", 25.30),
        (23, 59, "Shoulder", 25.30),
    ):
        local = datetime(2026, 1, 1, hour, minute, tzinfo=ZoneInfo("Australia/Brisbane"))
        period = find_period(ORIGIN_PERIODS, local.astimezone(timezone.utc), "import", "Australia/Brisbane")
        assert (period["label"], period["rate_cents_per_kwh"]) == (label, rate)


def test_day_grouping_uses_aest_across_utc_midnight():
    lines = aggregate_bill_lines(
        [{"captured_at": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc), "grid_import_kwh": 1, "grid_export_kwh": 0}],
        PERIODS,
        "Australia/Brisbane",
    )
    assert rollup_by_day_and_period(lines, "Australia/Brisbane")[0]["day"] == "2026-01-02"


def test_export_tier_applies_once_per_local_day_and_splits_crossing_line():
    priced = apply_daily_export_tier(
        [{"day": "2026-01-01", "period_label": "Solar export", "direction": "export", "kwh": 10.0, "amount_cents": -30.0, "rate_cents_per_kwh": 3.0}],
        8.0, 8.0, 3.0,
    )
    assert [(row["kwh"], row["rate_cents_per_kwh"], row["amount_cents"]) for row in priced] == [(8.0, 8.0, -64.0), (2.0, 3.0, -6.0)]


def test_same_day_flat_peak_export_keeps_its_rate_while_daytime_export_is_tiered():
    periods = [
        {"id": 10, "direction": "export", "label": "Peak battery export", "start_minute": 0, "end_minute": 540, "rate_cents_per_kwh": 20.0},
        {"id": 11, "direction": "export", "label": "Daytime solar export", "start_minute": 540, "end_minute": 960, "rate_cents_per_kwh": 3.0, "export_tier_kwh": 8.0, "export_tier_rate_cents_per_kwh": 8.0, "export_excess_rate_cents_per_kwh": 3.0},
    ]
    snapshots = [
        {"captured_at": datetime(2026, 1, 1, 1, tzinfo=ZoneInfo("Australia/Brisbane")).astimezone(timezone.utc), "grid_export_kwh": 2.0},
        {"captured_at": datetime(2026, 1, 1, 10, tzinfo=ZoneInfo("Australia/Brisbane")).astimezone(timezone.utc), "grid_export_kwh": 10.0},
    ]

    rows = apply_daily_export_tier(rollup_by_day_and_period(aggregate_bill_lines(snapshots, periods)))

    assert [(row["period_label"], row["kwh"], row["rate_cents_per_kwh"], row["amount_cents"]) for row in rows] == [
        ("Daytime solar export (tier 1)", 8.0, 8.0, -64.0),
        ("Daytime solar export (excess)", 2.0, 3.0, -6.0),
        ("Peak battery export", 2.0, 20.0, -40.0),
    ]


def test_origin_import_migration_preserves_active_plan_metadata_and_export_tier(tmp_path):
    db_path = tmp_path / "solarmax.db"
    service = SolarmaxService(db_path)
    with db_session(db_path) as conn:
        conn.execute(
            """UPDATE power_plans SET provider_name=?, plan_name=?, billing_start_day=?, billing_start_month=?,
               daily_supply_charge_cents=?, export_tier_kwh=?, export_tier_rate_cents_per_kwh=?,
               export_excess_rate_cents_per_kwh=?, notes=? WHERE id=1""",
            ("Origin", "Solar Boost", 13, 9, 178.0, 8.0, 8.0, 3.0, "Keep this metadata"),
        )
        conn.execute("DELETE FROM tou_periods WHERE plan_id=1")
        conn.executemany(
            "INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh) VALUES (1, ?, ?, ?, ?, ?)",
            [("import", "Old", 0, 1440, 99.0), ("export", "Solar export", 0, 1440, 3.0)],
        )
        conn.execute("DELETE FROM app_settings WHERE key='origin_import_tou_v1_applied'")

    init_db(db_path)

    with db_session(db_path) as conn:
        plan = dict(conn.execute("SELECT * FROM power_plans WHERE id=1").fetchone())
        imports = [tuple(row) for row in conn.execute(
            "SELECT label, start_minute, end_minute, rate_cents_per_kwh FROM tou_periods WHERE plan_id=1 AND direction='import' ORDER BY start_minute"
        )]
        exports = [tuple(row) for row in conn.execute(
            "SELECT label, start_minute, end_minute, rate_cents_per_kwh FROM tou_periods WHERE plan_id=1 AND direction='export'"
        )]

    assert plan == {
        "id": 1, "provider_name": "Origin", "plan_name": "Solar Boost", "billing_cycle": "monthly",
        "billing_start_day": 13, "billing_start_month": 9, "daily_supply_charge_cents": 178.0,
        "export_tier_kwh": 8.0, "export_tier_rate_cents_per_kwh": 8.0,
        "export_excess_rate_cents_per_kwh": 3.0, "notes": "Keep this metadata",
    }
    assert imports == list(ORIGIN_IMPORT_PERIODS)
    assert exports == [("Solar export", 0, 1440, 3.0)]


def test_export_tier_backfill_targets_solar_export_once_and_is_idempotent(tmp_path):
    db_path = tmp_path / "solarmax.db"
    SolarmaxService(db_path)
    with db_session(db_path) as conn:
        conn.execute("UPDATE power_plans SET export_tier_kwh=8, export_tier_rate_cents_per_kwh=8, export_excess_rate_cents_per_kwh=3 WHERE id=1")
        conn.execute("UPDATE tou_periods SET export_tier_kwh=0, export_tier_rate_cents_per_kwh=0, export_excess_rate_cents_per_kwh=0 WHERE plan_id=1")
        conn.execute("INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh) VALUES (1, 'export', 'Peak battery export', 0, 30, 20)")
        conn.execute("DELETE FROM app_settings WHERE key='export_tiers_to_tou_v1_applied'")

    init_db(db_path)
    with db_session(db_path) as conn:
        tiers = [tuple(row) for row in conn.execute("SELECT label, export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh FROM tou_periods WHERE plan_id=1 AND direction='export' ORDER BY label")]
        assert tiers == [("Peak battery export", 0.0, 0.0, 0.0), ("Solar export", 8.0, 8.0, 3.0)]
        conn.execute("UPDATE tou_periods SET export_tier_kwh=0, export_tier_rate_cents_per_kwh=0, export_excess_rate_cents_per_kwh=0 WHERE plan_id=1 AND label='Solar export'")

    init_db(db_path)
    with db_session(db_path) as conn:
        assert tuple(conn.execute("SELECT export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh FROM tou_periods WHERE plan_id=1 AND label='Solar export'").fetchone()) == (0.0, 0.0, 0.0)


def test_export_tier_backfill_skips_incomplete_legacy_tiers(tmp_path):
    db_path = tmp_path / "solarmax.db"
    SolarmaxService(db_path)
    with db_session(db_path) as conn:
        conn.execute("UPDATE power_plans SET export_tier_kwh=8, export_tier_rate_cents_per_kwh=0, export_excess_rate_cents_per_kwh=3 WHERE id=1")
        conn.execute("UPDATE tou_periods SET export_tier_kwh=0, export_tier_rate_cents_per_kwh=0, export_excess_rate_cents_per_kwh=0 WHERE plan_id=1 AND label='Solar export'")
        conn.execute("DELETE FROM app_settings WHERE key='export_tiers_to_tou_v1_applied'")

    init_db(db_path)
    with db_session(db_path) as conn:
        assert tuple(conn.execute("SELECT export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh FROM tou_periods WHERE plan_id=1 AND label='Solar export'").fetchone()) == (0.0, 0.0, 0.0)


def test_export_tier_backfill_no_match_is_marked_and_idempotent(tmp_path):
    db_path = tmp_path / "solarmax.db"
    SolarmaxService(db_path)
    with db_session(db_path) as conn:
        conn.execute("UPDATE power_plans SET export_tier_kwh=8, export_tier_rate_cents_per_kwh=8, export_excess_rate_cents_per_kwh=3 WHERE id=1")
        conn.execute("DELETE FROM tou_periods WHERE plan_id=1 AND direction='export'")
        conn.execute("DELETE FROM app_settings WHERE key='export_tiers_to_tou_v1_applied'")

    init_db(db_path)
    with db_session(db_path) as conn:
        assert conn.execute("SELECT 1 FROM tou_periods WHERE plan_id=1 AND direction='export'").fetchone() is None
        assert conn.execute("SELECT value FROM app_settings WHERE key='export_tiers_to_tou_v1_applied'").fetchone()[0] == "1"
        conn.execute("INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh) VALUES (1, 'export', 'Solar export', 0, 1440, 3)")

    init_db(db_path)
    with db_session(db_path) as conn:
        assert tuple(conn.execute("SELECT export_tier_kwh, export_tier_rate_cents_per_kwh, export_excess_rate_cents_per_kwh FROM tou_periods WHERE plan_id=1 AND label='Solar export'").fetchone()) == (0.0, 0.0, 0.0)


def test_source_change_is_a_zero_delta_baseline():
    deltas = SolarmaxService._deltas_for_reading(reading(200.0, True), reading(10.0, False))
    assert set(deltas.values()) == {0.0}


def test_billing_reconciles_today_grid_kwh_to_the_plant_meter(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    captured = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    today = captured.astimezone(ZoneInfo("Australia/Brisbane")).date().isoformat()
    with db_session(service.db_path) as conn:
        conn.execute(
            """INSERT INTO daily_counters
               (inverter_id, day, grid_export_kwh, solar_baseline_kwh,
                load_baseline_kwh, grid_import_baseline_kwh,
                grid_export_baseline_kwh, battery_charge_baseline_kwh,
                battery_discharge_baseline_kwh)
               VALUES (1, ?, ?, 0, 0, 0, 0, 0, 0)""",
            (today, 0.25),
        )
        conn.execute(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents)
               VALUES (1, ?, ?, 0, 0, 0, 9, 0, 0, -72)""",
            (captured.isoformat(), (captured + timedelta(minutes=30)).isoformat()),
        )

    bill = service.current_bill_summary()
    # Stale interval data must not override the plant-meter day total.  The
    # seeded default plan has a single flat full-day export tariff, so the
    # adjustment is priced at that rate rather than left unpriced.
    assert bill["today_grid_export_kwh"] == 0.25
    today_rows = [row for row in bill["rows"] if row["day"] == today]
    assert sum(row["kwh"] for row in today_rows if row["direction"] == "export") == 0.25
    reconciliation = next(row for row in today_rows if "Meter reconciliation" in row["period_label"])
    assert reconciliation["rate_cents_per_kwh"] == 8.0
    # Export is a credit, so the amount is negative.
    assert round(reconciliation["amount_cents"], 3) == -round(0.25 * 8.0, 3)
    # The overview chart must use the same finalized daily amount as Billing,
    # including reconciliation and export tiering.
    chart_row = next(row for row in service.chart_points() if row["day"] == today)
    daily_total = next(row for row in bill["daily_site_totals"] if row["day"] == today)
    assert chart_row["amount_cents"] == daily_total["daily_bill_amount_cents"]


def test_billing_reconciles_past_day_grid_kwh_to_daily_meter(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    site_zone = ZoneInfo("Australia/Brisbane")
    yesterday = datetime.now(site_zone).date() - timedelta(days=1)
    captured = datetime.combine(yesterday, datetime.min.time(), tzinfo=site_zone) + timedelta(hours=12)
    captured = captured.astimezone(timezone.utc)
    day = yesterday.isoformat()
    with db_session(service.db_path) as conn:
        _insert_daily_counter(conn, 1, day, (75.53, 0.0, 0.0, 29.43, 0.0, 0.0))
        conn.execute(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents)
               VALUES (1, ?, ?, 0, 0, 0, 0.02, 0, 0, 0)""",
            (captured.isoformat(), (captured + timedelta(minutes=30)).isoformat()),
        )

    bill = service.current_bill_summary()
    yesterday_rows = [row for row in bill["rows"] if row["day"] == day]
    assert round(sum(row["kwh"] for row in yesterday_rows if row["direction"] == "export"), 4) == 29.43
    reconciliation = [
        row for row in yesterday_rows
        if row["direction"] == "export" and "Meter reconciliation" in row["period_label"]
    ]
    assert [(row["kwh"], row["rate_cents_per_kwh"]) for row in reconciliation] == [(7.98, 8.0), (21.43, 3.0)]
    assert round(sum(row["amount_cents"] for row in reconciliation), 3) == -round(7.98 * 8 + 21.43 * 3, 3)


def test_meter_reconciliation_stays_unpriced_when_import_has_tou(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    site_zone = ZoneInfo("Australia/Brisbane")
    yesterday = datetime.now(site_zone).date() - timedelta(days=1)
    captured = (datetime.combine(yesterday, datetime.min.time(), tzinfo=site_zone) + timedelta(hours=12)).astimezone(timezone.utc)
    day = yesterday.isoformat()
    with db_session(service.db_path) as conn:
        # Replace the seeded import TOU with two periods so the direction is
        # not flat; keep a single flat full-day export period.
        conn.execute("DELETE FROM tou_periods WHERE direction='import'")
        conn.executemany(
            "INSERT INTO tou_periods (plan_id, direction, label, start_minute, end_minute, rate_cents_per_kwh) VALUES (1, ?, ?, ?, ?, ?)",
            [
                ("import", "Off-peak", 0, 540, 6.98),
                ("import", "Peak", 540, 1440, 47.78),
            ],
        )
        _insert_daily_counter(conn, 1, day, (0.0, 0.0, 5.0, 29.43, 0.0, 0.0))
        conn.execute(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents)
               VALUES (1, ?, ?, 0, 0, 4.98, 29.41, 0, 0, 0)""",
            (captured.isoformat(), (captured + timedelta(minutes=30)).isoformat()),
        )

    bill = service.current_bill_summary()
    yesterday_rows = [row for row in bill["rows"] if row["day"] == day]
    import_adj = next(
        row for row in yesterday_rows
        if row["direction"] == "import" and "Meter reconciliation" in row["period_label"]
    )
    export_adj = next(
        row for row in yesterday_rows
        if row["direction"] == "export" and "Meter reconciliation" in row["period_label"]
    )
    # Import has a TOU split, so its adjustment is unpriced; export is tiered.
    assert import_adj["kwh"] == 0.02
    assert import_adj["rate_cents_per_kwh"] is None
    assert import_adj["amount_cents"] is None
    assert export_adj["rate_cents_per_kwh"] == 3.0


def test_close_day_completes_rollups_and_reports_daily_counter_totals(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    site_zone = ZoneInfo("Australia/Brisbane")
    today = datetime.now(site_zone).date().isoformat()
    captured = datetime.now(timezone.utc) - timedelta(hours=2)
    with db_session(service.db_path) as conn:
        _insert_daily_counter(conn, 1, today, (75.53, 0.0, 1.2, 29.43, 0.0, 0.0))
        _insert_telemetry(conn, 1, captured.isoformat(), (100, 100, 100, 100, 100, 100))
        conn.execute(
            "UPDATE telemetry_raw SET delta_solar_kwh=1.5, delta_grid_export_kwh=0.4 WHERE inverter_id=1"
        )

    result = service.close_day()
    with db_session(service.db_path) as conn:
        rollup = conn.execute("SELECT solar_kwh, grid_export_kwh FROM telemetry_rollups WHERE inverter_id=1").fetchone()
        daily = conn.execute(
            "SELECT solar_kwh, grid_import_kwh, grid_export_kwh FROM daily_counters WHERE inverter_id=1 AND day=?",
            (today,),
        ).fetchone()

    assert tuple(rollup) == (1.5, 0.4)
    assert tuple(daily) == (75.53, 1.2, 29.43)
    assert result == {"day": today, "solar_kwh": 75.53, "grid_import_kwh": 1.2, "grid_export_kwh": 29.43}


def test_wipe_stale_days_removes_recent_telemetry_without_yesterday_counter(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    site_zone = ZoneInfo("Australia/Brisbane")
    today = datetime.now(site_zone).date()

    def insert_recent_rows(conn, local_day):
        captured = (datetime.combine(local_day, datetime.min.time(), tzinfo=site_zone) + timedelta(hours=12)).astimezone(timezone.utc)
        _insert_telemetry(conn, 1, captured.isoformat(), (1, 1, 1, 1, 1, 1))
        conn.execute(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents)
               VALUES (1, ?, ?, 0, 0, 0, 0, 0, 0, 0)""",
            (captured.isoformat(), (captured + timedelta(minutes=30)).isoformat()),
        )

    with db_session(service.db_path) as conn:
        insert_recent_rows(conn, today - timedelta(days=2))
        insert_recent_rows(conn, today - timedelta(days=1))
        insert_recent_rows(conn, today)

    service.wipe_stale_days()
    with db_session(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM telemetry_raw").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM telemetry_rollups").fetchone()[0] == 1
        _insert_daily_counter(conn, 1, (today - timedelta(days=1)).isoformat(), (0, 0, 0, 0, 0, 0))
        insert_recent_rows(conn, today)

    service.wipe_stale_days()
    with db_session(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM telemetry_raw").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM telemetry_rollups").fetchone()[0] == 2


def test_currency_parser_and_supply_charge_are_independent_of_import_kwh(tmp_path):
    assert _currency_to_cents("$1.78") == 178.0
    service = SolarmaxService(tmp_path / "solarmax.db")
    service.update_daily_supply_charge(1, 178)
    with db_session(service.db_path) as conn:
        conn.execute(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents)
               VALUES (1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:30:00+00:00', 0, 0, 2, 1, 0, 0, 0)"""
        )
    bill = service.current_bill_summary()
    imports = [row for row in bill["rows"] if row["direction"] == "import"]
    exports = [row for row in bill["rows"] if row["direction"] == "export"]
    assert sum(row["kwh"] for row in imports) == 2.0
    assert sum(row["kwh"] for row in exports) == 1.0
    # 00:00 UTC is 10:00 AEST, within Origin's 09:00–16:00 off-peak window.
    assert sum(row["amount_cents"] for row in imports) == 13.96


def _insert_telemetry(conn, inverter_id: int, captured_at: str, totals: tuple[float, ...]) -> None:
    conn.execute(
        """INSERT INTO telemetry_raw
           (inverter_id, captured_at, solar_kw, load_kw, grid_import_kw,
            grid_export_kw, battery_charge_kw, battery_discharge_kw,
            solar_total_kwh, load_total_kwh, grid_import_total_kwh,
            grid_export_total_kwh, battery_charge_total_kwh,
            battery_discharge_total_kwh, delta_solar_kwh, delta_load_kwh,
            delta_grid_import_kwh, delta_grid_export_kwh,
            delta_battery_charge_kwh, delta_battery_discharge_kwh, lifetime)
           VALUES (?, ?, 1, 2, 3, 4, 5, 6, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 1)""",
        (inverter_id, captured_at, *totals),
    )


def _insert_daily_counter(conn, inverter_id: int, day: str, totals: tuple[float, ...]) -> None:
    conn.execute(
        """INSERT INTO daily_counters
           (inverter_id, day, solar_kwh, load_kwh, grid_import_kwh,
            grid_export_kwh, battery_charge_kwh, battery_discharge_kwh,
            solar_baseline_kwh, load_baseline_kwh, grid_import_baseline_kwh,
            grid_export_baseline_kwh, battery_charge_baseline_kwh,
            battery_discharge_baseline_kwh)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0)""",
        (inverter_id, day, *totals),
    )


def test_daily_site_totals_use_all_six_authoritative_counters_and_daily_billing(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Australia/Brisbane")).date().isoformat()
    with db_session(service.db_path) as conn:
        conn.execute("UPDATE inverter_profiles SET reachable=1 WHERE id=1")
        _insert_daily_counter(conn, 1, today, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        _insert_telemetry(conn, 1, now.isoformat(), (100, 200, 300, 400, 500, 600))

    bill = service.current_bill_summary()
    daily = next(row for row in bill["daily_site_totals"] if row["day"] == today)
    assert daily == {
        "day": today,
        "solar_kwh": 1.0,
        "load_kwh": 2.0,
        "grid_import_kwh": 3.0,
        "grid_export_kwh": 4.0,
        "battery_charge_kwh": 5.0,
        "battery_discharge_kwh": 6.0,
        "daily_bill_amount_cents": -32.0,
    }
    overview = service.dashboard_state()["totals"]
    assert overview == {
        "solar_total_kwh": daily["solar_kwh"],
        "load_total_kwh": daily["load_kwh"],
        "grid_import_total_kwh": daily["grid_import_kwh"],
        "grid_export_total_kwh": daily["grid_export_kwh"],
        "battery_charge_total_kwh": daily["battery_charge_kwh"],
        "battery_discharge_total_kwh": daily["battery_discharge_kwh"],
    }


def test_no_active_plan_still_returns_authoritative_daily_site_totals(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", service)
    today = datetime.now(ZoneInfo("Australia/Brisbane")).date().isoformat()
    with db_session(service.db_path) as conn:
        conn.execute("UPDATE app_settings SET value='' WHERE key='active_plan_id'")
        _insert_daily_counter(conn, 1, today, (1.5, 2.5, 3.5, 4.5, 5.5, 6.5))

    bill = service.current_bill_summary()

    assert bill["plan"] is None
    assert bill["total_cents"] == 0.0
    assert bill["daily_site_totals"] == [{
        "day": today,
        "solar_kwh": 1.5,
        "load_kwh": 2.5,
        "grid_import_kwh": 3.5,
        "grid_export_kwh": 4.5,
        "battery_charge_kwh": 5.5,
        "battery_discharge_kwh": 6.5,
        "daily_bill_amount_cents": 0.0,
    }]
    assert bill["today_grid_import_kwh"] == 3.5
    assert bill["today_grid_export_kwh"] == 4.5

    from fastapi.testclient import TestClient
    from solarmax.main import app

    with TestClient(app) as client:
        response = client.get("/api/bill")
    assert response.status_code == 200
    assert response.json()["daily_site_totals"] == bill["daily_site_totals"]


def test_dashboard_totals_use_daily_enabled_inverter_counters_not_lifetime_values(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    second_id = service.upsert_inverter({"name": "Second", "model": "SigenStor EC 20", "adapter_kind": "sigenstor_ec_20_0_tp_au"})
    disabled_id = service.upsert_inverter({"name": "Old", "model": "SigenStor EC 20", "adapter_kind": "sigenstor_ec_20_0_tp_au", "enabled": False})
    today = datetime.now(ZoneInfo("Australia/Brisbane")).date().isoformat()
    with db_session(service.db_path) as conn:
        conn.execute("UPDATE inverter_profiles SET reachable=1 WHERE enabled=1")
        _insert_telemetry(conn, 1, "2026-01-01T00:01:00+00:00", (10000, 20000, 30000, 40000, 50000, 60000))
        _insert_telemetry(conn, second_id, "2026-01-01T00:01:00+00:00", (100000, 200000, 300000, 400000, 500000, 600000))
        _insert_telemetry(conn, disabled_id, "2026-01-01T00:01:00+00:00", (1000000, 2000000, 3000000, 4000000, 5000000, 6000000))
        _insert_daily_counter(conn, 1, today, (1, 2, 3, 4, 5, 6))
        _insert_daily_counter(conn, second_id, today, (10, 20, 30, 40, 50, 60))
        _insert_daily_counter(conn, disabled_id, today, (1000, 2000, 3000, 4000, 5000, 6000))
        conn.execute("UPDATE inverter_profiles SET reachable=1 WHERE id=?", (disabled_id,))

    state = service.dashboard_state()
    assert state["all_reachable"] is True
    assert state["totals"] == {
        "solar_total_kwh": 11.0, "load_total_kwh": 22.0,
        "grid_import_total_kwh": 33.0, "grid_export_total_kwh": 44.0,
        "battery_charge_total_kwh": 55.0, "battery_discharge_total_kwh": 66.0,
    }
    assert state["live"]["solar_kw"] == 2.0
    rendered = templates.get_template("dashboard.html").render(
        state=state, settings=state["settings"], bill=state["bill"], chart_points=[],
    )
    assert "Solar generation today" in rendered
    assert "11.00 kWh" in rendered
    assert "110000.00 kWh" not in rendered


def test_daily_counters_update_and_reset_at_site_local_midnight(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    before_midnight = reading(100.0, True)
    before_midnight.captured_at = datetime(2026, 1, 1, 13, 59, tzinfo=timezone.utc)  # 23:59 Brisbane
    after_midnight = reading(200.0, True)
    after_midnight.captured_at = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)  # 00:00 Brisbane
    later_that_day = reading(205.0, True)
    later_that_day.captured_at = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)

    with db_session(service.db_path) as conn:
        service._update_daily_counters(conn, 1, before_midnight)
        service._update_daily_counters(conn, 1, after_midnight)
        service._update_daily_counters(conn, 1, later_that_day)
        rows = conn.execute(
            "SELECT day, solar_kwh, solar_baseline_kwh FROM daily_counters WHERE inverter_id = 1 ORDER BY day"
        ).fetchall()

    assert [tuple(row) for row in rows] == [
        ("2026-01-01", 0.0, 100.0),
        ("2026-01-02", 5.0, 205.0),
    ]


def test_daily_grid_bootstraps_from_persisted_midnight_lifetime_baseline(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    site_zone = ZoneInfo("Australia/Brisbane")
    boundary = datetime.combine(
        datetime.now(site_zone).date(), datetime.min.time(), tzinfo=site_zone,
    ).astimezone(timezone.utc)
    with db_session(service.db_path) as conn:
        _insert_telemetry(
            conn, 1, boundary.isoformat(),
            (5000.0, 4000.0, 196.38, 137.79, 2000.0, 1000.0),
        )

    current = InverterReading(
        captured_at=boundary + timedelta(hours=10),
        solar_kw=8.4, load_kw=3.2, grid_import_kw=0.0, grid_export_kw=0.2,
        battery_charge_kw=5.2, battery_discharge_kw=0.0,
        solar_total_kwh=5075.52, load_total_kwh=4037.47,
        grid_import_total_kwh=196.43, grid_export_total_kwh=167.21,
        battery_charge_total_kwh=2027.15, battery_discharge_total_kwh=1018.48,
        lifetime=True,
        daily_totals_kwh={
            "solar": 75.52, "load": 37.47,
            "battery_charge": 27.15, "battery_discharge": 18.48,
        },
    )

    class OneReadingAdapter:
        def read(self, profile, previous):
            return current

    monkeypatch.setattr("solarmax.service.get_adapter", lambda kind: OneReadingAdapter())
    service.poll_once()

    state = service.dashboard_state()
    assert state["totals"] == {
        "solar_total_kwh": 75.52,
        "load_total_kwh": 37.47,
        "grid_import_total_kwh": 0.05,
        "grid_export_total_kwh": 29.42,
        "battery_charge_total_kwh": 27.15,
        "battery_discharge_total_kwh": 18.48,
    }
    bill = state["bill"]
    assert bill["today_grid_import_kwh"] == 0.05
    assert bill["today_grid_export_kwh"] == 29.42
    today = datetime.now(ZoneInfo("Australia/Brisbane")).date().isoformat()
    assert sum(row["kwh"] for row in bill["rows"] if row["day"] == today and row["direction"] == "import") == 0.05
    assert sum(row["kwh"] for row in bill["rows"] if row["day"] == today and row["direction"] == "export") == 29.42


def test_legacy_grid_midnight_baseline_survives_source_tracking_migration(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    sample = reading(167.20, True)
    sample.captured_at = datetime(2026, 1, 1, 20, tzinfo=timezone.utc)
    sample.grid_import_total_kwh = 196.43
    with db_session(service.db_path) as conn:
        conn.execute(
            """INSERT INTO daily_counters
               (inverter_id, day, grid_import_baseline_kwh, grid_export_baseline_kwh,
                solar_baseline_kwh, load_baseline_kwh, battery_charge_baseline_kwh,
                battery_discharge_baseline_kwh)
               VALUES (1, '2026-01-02', 190.0, 137.78, 0, 0, 0, 0)"""
        )
        service._update_daily_counters(conn, 1, sample)
        row = conn.execute(
            "SELECT grid_import_kwh, grid_export_kwh, grid_import_source, grid_export_source "
            "FROM daily_counters WHERE inverter_id=1 AND day='2026-01-02'"
        ).fetchone()

    assert tuple(row) == (6.43, 29.42, "lifetime", "lifetime")


def test_stale_lifetime_sample_is_not_used_as_a_midnight_baseline(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    site_zone = ZoneInfo("Australia/Brisbane")
    boundary = datetime.combine(
        datetime.now(site_zone).date(), datetime.min.time(), tzinfo=site_zone,
    ).astimezone(timezone.utc)
    with db_session(service.db_path) as conn:
        _insert_telemetry(
            conn, 1, (boundary - timedelta(hours=2)).isoformat(),
            (5000.0, 4000.0, 190.0, 137.78, 2000.0, 1000.0),
        )

    current = reading(167.20, True)
    current.captured_at = boundary + timedelta(hours=10)
    current.grid_import_total_kwh = 196.43

    class OneReadingAdapter:
        def read(self, profile, previous):
            return current

    monkeypatch.setattr("solarmax.service.get_adapter", lambda kind: OneReadingAdapter())
    service.poll_once()

    totals = service.dashboard_state()["totals"]
    assert totals["grid_import_total_kwh"] == 0.0
    assert totals["grid_export_total_kwh"] == 0.0


def test_dashboard_hides_running_totals_when_an_enabled_inverter_is_unreachable(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    with db_session(service.db_path) as conn:
        conn.execute("UPDATE inverter_profiles SET reachable=0 WHERE id=1")
        _insert_telemetry(conn, 1, "2026-01-01T00:00:00+00:00", (10, 20, 30, 40, 50, 60))

    state = service.dashboard_state()
    assert state["all_reachable"] is False
    assert state["totals"] is None
    rendered = templates.get_template("dashboard.html").render(
        state=state, settings=state["settings"], bill=state["bill"], chart_points=[],
    )
    assert "Solar generation today</span><strong>—</strong>" in rendered


def test_daily_counters_keep_energy_across_source_changes_and_counter_resets(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    started = reading(1000.0, True)
    started.captured_at = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    started.daily_totals_kwh = {"solar": 75.0}
    advanced = reading(1029.42, True)
    advanced.captured_at = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    advanced.daily_totals_kwh = {"solar": 76.0}
    reset = reading(0.0, True)
    reset.captured_at = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
    reset.daily_totals_kwh = {"solar": 0.0}
    recovered = reading(1.0, True)
    recovered.captured_at = datetime(2026, 1, 1, 14, 3, tzinfo=timezone.utc)
    recovered.daily_totals_kwh = {"solar": 2.0}
    session_source = reading(5000.0, False)
    session_source.captured_at = datetime(2026, 1, 1, 14, 4, tzinfo=timezone.utc)
    session_advanced = reading(5001.0, False)
    session_advanced.captured_at = datetime(2026, 1, 1, 14, 5, tzinfo=timezone.utc)

    with db_session(service.db_path) as conn:
        for sample in (started, advanced, reset, recovered, session_source, session_advanced):
            service._update_daily_counters(conn, 1, sample)
        row = conn.execute(
            "SELECT solar_kwh, grid_export_kwh, solar_source FROM daily_counters WHERE inverter_id=1"
        ).fetchone()

    # 75 direct daily kWh + 1 kWh before the daily-register reset + 2 kWh
    # after it + 1 kWh from the new session source. Grid is lifetime-only.
    assert tuple(row) == (79.0, 31.42, "session")


def test_dashboard_today_shape_uses_direct_daily_and_lifetime_grid_baseline(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    now = datetime.now(timezone.utc)

    def sample(export_total: float) -> InverterReading:
        return InverterReading(
            captured_at=now,
            solar_kw=8.4, load_kw=3.2, grid_import_kw=0.0, grid_export_kw=0.2,
            battery_charge_kw=5.2, battery_discharge_kw=0.0,
            solar_total_kwh=5000.0, load_total_kwh=4000.0,
            grid_import_total_kwh=3000.0, grid_export_total_kwh=export_total,
            battery_charge_total_kwh=2000.0, battery_discharge_total_kwh=1000.0,
            lifetime=True,
            daily_totals_kwh={
                "solar": 75.0, "load": 42.5,
                "battery_charge": 21.0, "battery_discharge": 14.0,
            },
        )

    class SequenceAdapter:
        def __init__(self):
            self.samples = [sample(100.0), sample(129.42)]

        def read(self, profile, previous):
            return self.samples.pop(0)

    adapter = SequenceAdapter()
    monkeypatch.setattr("solarmax.service.get_adapter", lambda kind: adapter)
    service.poll_once()
    service.poll_once()

    totals = service.dashboard_state()["totals"]
    assert totals == {
        "solar_total_kwh": 75.0,
        "load_total_kwh": 42.5,
        "grid_import_total_kwh": 0.0,
        "grid_export_total_kwh": 29.42,
        "battery_charge_total_kwh": 21.0,
        "battery_discharge_total_kwh": 14.0,
    }


def test_sigenstor_adapter_marks_lifetime_and_direct_daily_registers(monkeypatch):
    power = {30035: 8400, 30284: 3200, 30005: -200, 30037: 5200}
    lifetime = {30088: 500000, 30094: 400000, 30260: 19643, 30264: 16721, 30200: 200000, 30204: 100000}
    daily = {(1, 31509): 7552, (247, 30092): 4250, (1, 30566): 2100, (1, 30572): 1400}

    def registers(value, quantity):
        raw = value & ((1 << (quantity * 16)) - 1)
        return [(raw >> shift) & 0xFFFF for shift in range((quantity - 1) * 16, -1, -16)]

    reads = []

    def read_registers(host, unit_id, address, quantity):
        reads.append((unit_id, address, quantity))
        if address in power:
            return registers(power[address], quantity)
        if address in lifetime:
            return registers(lifetime[address], quantity)
        if (unit_id, address) in daily:
            return registers(daily[(unit_id, address)], quantity)
        if address in (30014, 30003):
            return [0]
        raise AssertionError((unit_id, address, quantity))

    monkeypatch.setattr("solarmax.inverters.sigenstor_ec_20_0_tp_au.read_input_registers", read_registers)
    result = SigenStorEC20TPAUAdapter()._read_real("inverter", None)

    assert result.lifetime is True
    assert result.solar_total_kwh == 5000.0
    assert result.grid_import_total_kwh == 196.43
    assert result.grid_export_total_kwh == 167.21
    assert result.daily_totals_kwh == {
        "solar": 75.52, "load": 42.5,
        "battery_charge": 21.0, "battery_discharge": 14.0,
    }
    assert reads == [
        (247, 30035, 2), (247, 30284, 2), (247, 30005, 2), (247, 30037, 2),
        (247, 30088, 4), (247, 30094, 4), (247, 30260, 4), (247, 30264, 4),
        (247, 30200, 4), (247, 30204, 4),
        (1, 31509, 2), (247, 30092, 2), (1, 30566, 2), (1, 30572, 2),
        (247, 30014, 1), (247, 30003, 1),
    ]
    assert (247, 30272) not in reads
    assert (247, 30216) not in reads
    assert (247, 30220) not in reads
    assert (247, 30128) not in reads
    # Unit 1 has 30554/30560 "daily export/import" counters, but they are
    # inverter-terminal energy, not the plant grid sensor; the adapter must
    # not substitute them for the plant grid lifetime registers.
    assert (1, 30554, 2) not in reads
    assert (1, 30560, 2) not in reads


def test_sigenstor_adapter_falls_back_when_preferred_registers_are_unavailable(monkeypatch):
    power = {30035: 8400, 30284: 3200, 30005: -200, 30037: 5200}
    lifetime = {30088: 500000, 30094: 400000, 30216: 19643, 30220: 16721, 30200: 200000, 30204: 100000}
    daily = {(247, 30272): 7552, (247, 30092): 4250, (1, 30566): 2100, (1, 30572): 1400}

    def registers(value, quantity):
        raw = value & ((1 << (quantity * 16)) - 1)
        return [(raw >> shift) & 0xFFFF for shift in range((quantity - 1) * 16, -1, -16)]

    reads = []

    def read_registers(host, unit_id, address, quantity):
        reads.append((unit_id, address, quantity))
        if (unit_id, address) in ((247, 30260), (247, 30264), (1, 31509)):
            raise ModbusError("register unavailable")
        if address in power:
            return registers(power[address], quantity)
        if address in lifetime:
            return registers(lifetime[address], quantity)
        if (unit_id, address) in daily:
            return registers(daily[(unit_id, address)], quantity)
        if address in (30014, 30003):
            return [0]
        raise AssertionError((unit_id, address, quantity))

    monkeypatch.setattr("solarmax.inverters.sigenstor_ec_20_0_tp_au.read_input_registers", read_registers)
    result = SigenStorEC20TPAUAdapter()._read_real("inverter", None)

    assert result.lifetime is True
    assert result.solar_total_kwh == 5000.0
    assert result.load_total_kwh == 4000.0
    assert result.grid_import_total_kwh == 196.43
    assert result.grid_export_total_kwh == 167.21
    assert result.battery_charge_total_kwh == 2000.0
    assert result.battery_discharge_total_kwh == 1000.0
    assert result.daily_totals_kwh == {
        "solar": 75.52, "load": 42.5,
        "battery_charge": 21.0, "battery_discharge": 14.0,
    }
    assert reads == [
        (247, 30035, 2), (247, 30284, 2), (247, 30005, 2), (247, 30037, 2),
        (247, 30088, 4), (247, 30094, 4),
        (247, 30260, 4), (247, 30216, 4),
        (247, 30264, 4), (247, 30220, 4),
        (247, 30200, 4), (247, 30204, 4),
        (1, 31509, 2), (247, 30272, 2), (247, 30092, 2),
        (1, 30566, 2), (1, 30572, 2),
        (247, 30014, 1), (247, 30003, 1),
    ]
    assert (247, 30128, 2) not in reads
