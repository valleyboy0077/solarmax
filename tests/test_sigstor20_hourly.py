from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import solarmax.main as main
from starlette.requests import Request
from solarmax.config import RuntimeConfig
from solarmax.db import db_session
from solarmax.models import Sigstor20HourlyResponse
from solarmax.service import SolarmaxService
from solarmax.sigstor20_hourly import COUNTER_COLUMNS, day_bounds_utc


FIELDS = tuple(COUNTER_COLUMNS)
DB_COLUMNS = tuple(COUNTER_COLUMNS.values())


def _insert_sample(
    conn,
    inverter_id: int,
    captured_at: datetime,
    counters: dict[str, float],
    soc: float | None = 50.0,
    *,
    lifetime: int = 1,
) -> None:
    conn.execute(
        f"""INSERT INTO telemetry_raw
            (inverter_id, captured_at, solar_kw, load_kw, grid_import_kw,
             grid_export_kw, battery_charge_kw, battery_discharge_kw,
             battery_level_percent, {', '.join(DB_COLUMNS)},
             delta_solar_kwh, delta_load_kwh, delta_grid_import_kwh,
             delta_grid_export_kwh, delta_battery_charge_kwh,
             delta_battery_discharge_kwh, lifetime)
            VALUES (?, ?, 0, 0, 0, 0, 0, 0, ?, {', '.join('?' for _ in DB_COLUMNS)},
                    0, 0, 0, 0, 0, 0, ?)""",
        (inverter_id, captured_at.isoformat(), soc, *(counters[field] for field in FIELDS), lifetime),
    )


def _counter_values(value: float = 0.0) -> dict[str, float]:
    return {key: value for key in FIELDS}


def _series_for_day(day: date, zone: ZoneInfo, upto_hour: int | None = None) -> list[tuple[datetime, dict[str, float], float]]:
    start, end = day_bounds_utc(day, zone)
    boundaries: list[datetime] = []
    cursor = start
    while cursor < end and (upto_hour is None or len(boundaries) <= upto_hour):
        boundaries.append(cursor)
        cursor += timedelta(hours=1)
    if cursor == end and (upto_hour is None or len(boundaries) <= upto_hour):
        boundaries.append(end)
    result = []
    counters = {field: 100.0 for field in FIELDS}
    for index, captured in enumerate(boundaries):
        if index:
            for field in FIELDS:
                counters[field] += 1.25 if field == "solar_kwh" and index == 13 else 0.5
        result.append((captured, dict(counters), min(99.0, 50.0 + index)))
    return result


def _create_service(tmp_path) -> SolarmaxService:
    return SolarmaxService(tmp_path / "hourly.db")


def test_noon_to_one_hourly_attribution_and_cumulative_reconcile(tmp_path):
    service = _create_service(tmp_path)
    day = date(2026, 8, 12)
    zone = ZoneInfo("Australia/Brisbane")
    samples = _series_for_day(day, zone, upto_hour=13)
    with db_session(service.db_path) as conn:
        for captured, counters, soc in samples:
            _insert_sample(conn, 1, captured, counters, soc)

    data = service.sigstor20_hourly_data(day, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    noon_to_one = data["rows"][12]

    assert data["has_readings"] is True
    assert noon_to_one["starts_at"].astimezone(zone).hour == 12
    assert noon_to_one["ends_at"].astimezone(zone).hour == 13
    assert noon_to_one["hourly_kwh"]["solar_kwh"] == 1.25
    assert noon_to_one["hourly_kwh"]["load_kwh"] == 0.5
    assert noon_to_one["cumulative_kwh"]["solar_kwh"] == sum(
        row["hourly_kwh"]["solar_kwh"] for row in data["rows"][:13]
    )
    assert noon_to_one["cumulative_kwh"]["solar_kwh"] == 7.25
    assert noon_to_one["ending_battery_soc_percent"] == 63.0
    assert noon_to_one["battery_direction"] == "Charging"


def test_empty_date_latest_date_and_read_only_get_api(tmp_path, monkeypatch):
    service = _create_service(tmp_path)
    monkeypatch.setattr(main, "service", service)
    monkeypatch.setattr(service, "poll_once", lambda: (_ for _ in ()).throw(AssertionError("hourly GET polled hardware")))
    earlier = datetime(2026, 8, 1, 23, tzinfo=timezone.utc)
    later = datetime(2026, 9, 26, 0, tzinfo=timezone.utc)
    with db_session(service.db_path) as conn:
        _insert_sample(conn, 1, earlier, _counter_values(10))
        _insert_sample(conn, 1, later, _counter_values(20))

    payload = Sigstor20HourlyResponse.model_validate(main.api_sigstor20_hourly())
    empty = Sigstor20HourlyResponse.model_validate(
        service.sigstor20_hourly_data(date(2026, 9, 25))
    )

    assert payload.timezone == "Australia/Brisbane"
    assert payload.first_day == date(2026, 8, 2)
    assert payload.latest_day == date(2026, 9, 26)
    assert payload.selected_day == payload.latest_day
    assert empty.has_readings is False
    assert empty.rows == []


def test_legacy_hourly_page_renders_get_date_and_accessible_mode_controls(tmp_path, monkeypatch):
    service = _create_service(tmp_path)
    monkeypatch.setattr(main, "service", service)
    monkeypatch.setattr(main, "config", RuntimeConfig(webui_mode="legacy"))
    request = Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/sigstor20-hourly",
        "raw_path": b"/sigstor20-hourly", "query_string": b"", "root_path": "",
        "headers": [], "client": ("test", 1000), "server": ("test", 80),
    })

    response = main.sigstor20_hourly_page(request)
    markup = response.body.decode()

    assert response.status_code == 200
    assert 'method="get" action="/sigstor20-hourly"' in markup
    assert 'type="date" name="day"' in markup
    assert 'type="radio" name="mode" value="both" checked' in markup
    assert "No stored telemetry for this local date" in markup
    assert "$" not in markup


def test_stale_hour_boundary_is_unavailable(tmp_path):
    service = _create_service(tmp_path)
    day = date(2026, 8, 12)
    zone = ZoneInfo("Australia/Brisbane")
    noon = datetime.combine(day, time(12), tzinfo=zone)
    before = _counter_values(100)
    after = _counter_values(110)
    with db_session(service.db_path) as conn:
        _insert_sample(conn, 1, noon, before)
        _insert_sample(conn, 1, noon + timedelta(minutes=40), after)

    data = service.sigstor20_hourly_data(day, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    noon_row = data["rows"][12]

    assert noon_row["hourly_kwh"]["solar_kwh"] is None
    assert noon_row["coverage_status"] == "unavailable"


def test_counter_reset_invalidates_only_the_affected_subtotal(tmp_path):
    service = _create_service(tmp_path)
    day = date(2026, 8, 12)
    zone = ZoneInfo("Australia/Brisbane")
    noon = datetime.combine(day, time(12), tzinfo=zone)
    before = _counter_values(100)
    reset = _counter_values(110)
    reset["solar_kwh"] = 5
    continued = _counter_values(120)
    continued["solar_kwh"] = 7
    with db_session(service.db_path) as conn:
        _insert_sample(conn, 1, noon, before)
        _insert_sample(conn, 1, noon + timedelta(hours=1), reset)
        _insert_sample(conn, 1, noon + timedelta(hours=2), continued)

    data = service.sigstor20_hourly_data(day, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    noon_row = data["rows"][12]
    one_row = data["rows"][13]

    assert noon_row["hourly_kwh"]["solar_kwh"] is None
    assert noon_row["hourly_kwh"]["load_kwh"] == 10.0
    assert noon_row["coverage_status"] == "partial"
    assert "reset" in noon_row["coverage_note"]
    assert one_row["hourly_kwh"]["solar_kwh"] == 2.0
    assert one_row["cumulative_kwh"]["solar_kwh"] is None


def test_current_partial_hour_and_future_rows_use_only_stored_samples(tmp_path):
    service = _create_service(tmp_path)
    zone = ZoneInfo("Australia/Brisbane")
    now = datetime(2026, 8, 12, 12, 31, tzinfo=zone)
    day = now.date()
    baseline = _counter_values(20)
    current = _counter_values(21)
    with db_session(service.db_path) as conn:
        _insert_sample(conn, 1, datetime.combine(day, time(12), tzinfo=zone), baseline, 60)
        _insert_sample(conn, 1, datetime.combine(day, time(12, 30), tzinfo=zone), current, 62)

    data = service.sigstor20_hourly_data(day, now=now)
    current_row = data["rows"][12]
    future_row = data["rows"][13]

    assert current_row["is_partial"] is True
    assert current_row["coverage_status"] == "partial"
    assert current_row["hourly_kwh"]["solar_kwh"] == 1.0
    assert current_row["ending_battery_soc_percent"] == 62.0
    assert future_row["is_future"] is True
    assert future_row["hourly_kwh"]["solar_kwh"] is None
    assert future_row["cumulative_kwh"]["solar_kwh"] is None


def test_historical_disabled_inverters_are_included_but_multi_inverter_soc_is_withheld(tmp_path):
    service = _create_service(tmp_path)
    second_id = service.upsert_inverter({"name": "Second", "adapter_kind": "sigenstor_ec_20_0_tp_au"})
    day = date(2026, 8, 12)
    zone = ZoneInfo("Australia/Brisbane")
    start = datetime.combine(day, time(10), tzinfo=zone)
    with db_session(service.db_path) as conn:
        conn.execute("UPDATE inverter_profiles SET enabled=0, reachable=0")
        for inverter_id, base in ((1, 100), (second_id, 200)):
            _insert_sample(conn, inverter_id, start, _counter_values(base), 40)
            _insert_sample(conn, inverter_id, start + timedelta(hours=1), _counter_values(base + 2), 65)

    row = service.sigstor20_hourly_data(day, now=datetime(2026, 9, 25, tzinfo=timezone.utc))["rows"][10]

    assert row["hourly_kwh"]["grid_import_kwh"] == 4.0
    assert row["observed_inverter_count"] == 2
    assert row["ending_battery_soc_percent"] is None
    assert row["battery_direction"] == "—"


def test_hour_intervals_remain_ordered_and_distinct_on_dst_days(tmp_path):
    service = _create_service(tmp_path)
    service.save_app_settings({"site_timezone": "Australia/Sydney"})
    zone = ZoneInfo("Australia/Sydney")

    for day, expected_hours in ((date(2026, 10, 4), 23), (date(2026, 4, 5), 25)):
        samples = _series_for_day(day, zone)
        with db_session(service.db_path) as conn:
            for captured, counters, soc in samples:
                _insert_sample(conn, 1, captured, counters, soc)
        data = service.sigstor20_hourly_data(day, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        starts = [row["starts_at"].astimezone(timezone.utc) for row in data["rows"]]

        assert len(data["rows"]) == expected_hours
        assert starts == sorted(starts)
        assert len({row["hour_label"] for row in data["rows"]}) == expected_hours
