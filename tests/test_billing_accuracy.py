from datetime import datetime, timezone

from solarmax.billing import aggregate_bill_lines, find_period, rollup_by_day_and_period
from solarmax.inverters.base import InverterReading
from solarmax.service import SolarmaxService
from solarmax.main import _currency_to_cents
from solarmax.db import db_session


PERIODS = [
    {"direction": "import", "label": "Off-peak", "start_minute": 0, "end_minute": 960, "rate_cents_per_kwh": 10},
    {"direction": "import", "label": "Peak", "start_minute": 960, "end_minute": 1260, "rate_cents_per_kwh": 47.78},
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


def test_day_grouping_uses_aest_across_utc_midnight():
    lines = aggregate_bill_lines(
        [{"captured_at": datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc), "grid_import_kwh": 1, "grid_export_kwh": 0}],
        PERIODS,
        "Australia/Brisbane",
    )
    assert rollup_by_day_and_period(lines, "Australia/Brisbane")[0]["day"] == "2026-01-02"


def test_source_change_is_a_zero_delta_baseline():
    deltas = SolarmaxService._deltas_for_reading(reading(200.0, True), reading(10.0, False))
    assert set(deltas.values()) == {0.0}


def test_currency_parser_accepts_dollar_amounts():
    assert _currency_to_cents("$1.78") == 178.0
    assert _currency_to_cents(" 2.5 ") == 250.0


def test_currency_parser_rejects_more_than_two_decimal_places():
    import pytest

    with pytest.raises(ValueError):
        _currency_to_cents("$1.789")


def test_supply_charge_is_added_once_per_local_day_to_bill_and_chart(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    service.update_daily_supply_charge(1, 178)
    with db_session(service.db_path) as conn:
        conn.executemany(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (1, "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00", 0, 0, 1, 0, 0, 0, 10),
                (1, "2026-01-01T00:30:00+00:00", "2026-01-01T01:00:00+00:00", 0, 0, 1, 0, 0, 0, 20),
            ],
        )

    bill = service.current_bill_summary()
    assert bill["supply_charge_cents"] == 178
    assert bill["supply_charge_days"] == 1
    assert sum(row["amount_cents"] for row in bill["rows"]) == 228.6
    chart = service.chart_points()
    assert chart == [{"day": "2026-01-01", "amount_cents": 208.0}]
