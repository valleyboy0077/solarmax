"""Hourly view calculations from persisted lifetime-counter snapshots."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


BOUNDARY_SAMPLE_MAX_AGE = timedelta(minutes=15)
COUNTER_COLUMNS = {
    "solar_kwh": "solar_total_kwh",
    "load_kwh": "load_total_kwh",
    "grid_import_kwh": "grid_import_total_kwh",
    "grid_export_kwh": "grid_export_total_kwh",
    "battery_charge_kwh": "battery_charge_total_kwh",
    "battery_discharge_kwh": "battery_discharge_total_kwh",
}


def parse_capture(value: str | datetime) -> datetime:
    captured = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    return captured.astimezone(timezone.utc)


def day_bounds_utc(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _hours_for_day(day: date, zone: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Build unambiguous elapsed-hour intervals across local DST transitions."""

    start, end = day_bounds_utc(day, zone)
    intervals: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        next_boundary = min(cursor + timedelta(hours=1), end)
        intervals.append((cursor, next_boundary))
        cursor = next_boundary
    return intervals


def _local_label(start: datetime, end: datetime, zone: ZoneInfo) -> str:
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)

    def offset_label(value: datetime) -> str:
        offset = value.utcoffset() or timedelta(0)
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "−"
        hours, minutes = divmod(abs(total_minutes), 60)
        return f"UTC{sign}{hours:02d}:{minutes:02d}"

    return (
        f"{local_start:%H:%M}–{local_end:%H:%M} "
        f"({local_start.tzname()} {offset_label(local_start)})"
    )


def _finite_counter(sample: dict[str, Any] | None, column: str) -> float | None:
    if sample is None:
        return None
    value = sample.get(column)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _soc(sample: dict[str, Any] | None) -> float | None:
    if sample is None:
        return None
    value = sample.get("battery_level_percent")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _sample_at_or_before(
    samples: list[dict[str, Any]], boundary: datetime, cutoff: datetime
) -> tuple[dict[str, Any] | None, float | None]:
    eligible = [sample for sample in samples if sample["_captured_utc"] <= boundary and sample["_captured_utc"] <= cutoff]
    if not eligible:
        return None, None
    sample = eligible[-1]
    age = max(0.0, (boundary - sample["_captured_utc"]).total_seconds())
    if age > BOUNDARY_SAMPLE_MAX_AGE.total_seconds():
        return None, age
    return sample, age


def build_sigstor20_hourly_response(
    *,
    day: date,
    timezone_name: str,
    day_samples: list[dict[str, Any]],
    lifetime_samples: list[dict[str, Any]],
    first_day: date | None,
    latest_day: date | None,
    latest_observation_at: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """Calculate visible hour totals without using rollups or daily registers."""

    zone = ZoneInfo(timezone_name)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    now_local = now_utc.astimezone(zone)
    day_start, day_end = day_bounds_utc(day, zone)
    if day == now_local.date():
        observation_cutoff = now_utc
    elif day == latest_day and latest_observation_at is not None:
        latest_utc = parse_capture(latest_observation_at)
        observation_cutoff = latest_utc if latest_utc > now_utc else day_end
    else:
        observation_cutoff = day_end

    day_rows: list[dict[str, Any]] = []
    for original in day_samples:
        row = dict(original)
        captured = parse_capture(row["captured_at"])
        if day_start <= captured < day_end:
            day_rows.append(row)

    inverter_ids = sorted({int(row["inverter_id"]) for row in day_rows})
    sample_count = len(day_rows)
    has_readings = bool(day_rows)
    if not has_readings:
        return {
            "timezone": timezone_name,
            "selected_day": day,
            "first_day": first_day,
            "latest_day": latest_day,
            "latest_observation_at": latest_observation_at,
            "has_readings": False,
            "sample_count": 0,
            "observed_inverter_count": 0,
            "boundary_sample_max_age_seconds": int(BOUNDARY_SAMPLE_MAX_AGE.total_seconds()),
            "rows": [],
        }

    by_inverter: dict[int, list[dict[str, Any]]] = {inverter_id: [] for inverter_id in inverter_ids}
    for original in lifetime_samples:
        row = dict(original)
        if int(row.get("lifetime", 0) or 0) != 1:
            continue
        inverter_id = int(row["inverter_id"])
        if inverter_id not in by_inverter:
            continue
        captured = parse_capture(row["captured_at"])
        if captured < day_start - BOUNDARY_SAMPLE_MAX_AGE or captured > day_end:
            continue
        row["_captured_utc"] = captured
        by_inverter[inverter_id].append(row)
    for samples in by_inverter.values():
        samples.sort(key=lambda sample: (sample["_captured_utc"], int(sample.get("id", 0))))

    cumulative = {key: 0.0 for key in COUNTER_COLUMNS}
    cumulative_valid = {key: True for key in COUNTER_COLUMNS}
    response_rows: list[dict[str, Any]] = []
    for hour_index, (start, end) in enumerate(_hours_for_day(day, zone)):
        is_future = start >= observation_cutoff
        is_partial = not is_future and end > observation_cutoff
        effective_end = min(end, observation_cutoff)
        start_samples: dict[int, dict[str, Any] | None] = {}
        end_samples: dict[int, dict[str, Any] | None] = {}
        start_ages: list[float] = []
        end_ages: list[float] = []
        start_valid = True
        end_valid = True

        if not is_future:
            for inverter_id, samples in by_inverter.items():
                start_sample, start_age = _sample_at_or_before(samples, start, observation_cutoff)
                end_sample, end_age = _sample_at_or_before(samples, effective_end, observation_cutoff)
                start_samples[inverter_id] = start_sample
                end_samples[inverter_id] = end_sample
                if start_sample is None:
                    start_valid = False
                elif start_age is not None:
                    start_ages.append(start_age)
                if end_sample is None:
                    end_valid = False
                elif end_age is not None:
                    end_ages.append(end_age)

        hourly: dict[str, float | None] = {}
        reset_detected = False
        for key, column in COUNTER_COLUMNS.items():
            total = 0.0
            metric_valid = not is_future and start_valid and end_valid
            if metric_valid:
                for inverter_id in inverter_ids:
                    start_sample = start_samples[inverter_id]
                    end_sample = end_samples[inverter_id]
                    assert start_sample is not None and end_sample is not None
                    if end_sample["_captured_utc"] <= start_sample["_captured_utc"]:
                        metric_valid = False
                        break
                    before = _finite_counter(start_sample, column)
                    after = _finite_counter(end_sample, column)
                    if before is None or after is None:
                        metric_valid = False
                        break
                    if after < before:
                        reset_detected = True
                        metric_valid = False
                        break
                    total += after - before
            hourly[key] = round(total, 6) if metric_valid else None
            if metric_valid and cumulative_valid[key]:
                cumulative[key] = round(cumulative[key] + total, 6)
            else:
                cumulative_valid[key] = False

        cumulative_values = {
            key: cumulative[key] if cumulative_valid[key] else None
            for key in COUNTER_COLUMNS
        }
        ending_soc: float | None = None
        battery_direction = "—"
        if len(inverter_ids) == 1 and not is_future:
            only_id = inverter_ids[0]
            start_sample = start_samples.get(only_id)
            end_sample = end_samples.get(only_id)
            ending_soc = _soc(end_sample)
            start_soc = _soc(start_sample)
            if start_soc is not None and ending_soc is not None and end_sample is not None and start_sample is not None:
                if end_sample["_captured_utc"] > start_sample["_captured_utc"]:
                    change = ending_soc - start_soc
                    if change > 0.05:
                        battery_direction = "Charging"
                    elif change < -0.05:
                        battery_direction = "Discharging"

        if is_future:
            coverage_status = "future"
            coverage_note = "This local hour is after the latest stored observation cutoff."
        elif not start_valid or not end_valid:
            coverage_status = "unavailable"
            coverage_note = "A fresh lifetime-counter snapshot is missing at one or both hour boundaries."
        elif any(value is None for value in hourly.values()):
            coverage_status = "partial"
            coverage_note = (
                "At least one lifetime counter reset, was invalid, or lacked a usable boundary sample."
                if reset_detected
                else "At least one lifetime counter could not be reconciled across the boundary snapshots."
            )
        elif is_partial:
            coverage_status = "partial"
            coverage_note = "Current partial hour; values end at the latest fresh stored snapshot."
        else:
            coverage_status = "complete"
            coverage_note = "Both values use fresh stored lifetime-counter snapshots at the hour boundaries."

        response_rows.append({
            "hour_index": hour_index,
            "hour_label": _local_label(start, end, zone),
            "starts_at": start.astimezone(zone),
            "ends_at": end.astimezone(zone),
            "hourly_kwh": hourly,
            "cumulative_kwh": cumulative_values,
            "ending_battery_soc_percent": ending_soc,
            "battery_direction": battery_direction,
            "coverage_status": coverage_status,
            "coverage_note": coverage_note,
            "is_partial": is_partial,
            "is_future": is_future,
            "sample_count": sample_count,
            "observed_inverter_count": len(inverter_ids),
            "start_boundary_max_age_seconds": max(start_ages) if start_ages else None,
            "end_boundary_max_age_seconds": max(end_ages) if end_ages else None,
        })

    return {
        "timezone": timezone_name,
        "selected_day": day,
        "first_day": first_day,
        "latest_day": latest_day,
        "latest_observation_at": latest_observation_at,
        "has_readings": True,
        "sample_count": sample_count,
        "observed_inverter_count": len(inverter_ids),
        "boundary_sample_max_age_seconds": int(BOUNDARY_SAMPLE_MAX_AGE.total_seconds()),
        "rows": response_rows,
    }
