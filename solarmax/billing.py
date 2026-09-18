"""Billing and TOU calculation helpers."""
from __future__ import annotations

from collections import defaultdict
import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


DEFAULT_SITE_TIMEZONE = "Australia/Brisbane"


def site_time(when: datetime, site_timezone: str = DEFAULT_SITE_TIMEZONE) -> datetime:
    """Convert a stored UTC timestamp to the site's wall-clock timezone."""

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(ZoneInfo(site_timezone))


def minutes_since_midnight(when: datetime) -> int:
    """Return minutes from midnight in local wall-clock time."""

    return when.hour * 60 + when.minute


def bucket_start(when: datetime) -> datetime:
    """Round down to the previous half-hour boundary."""

    return when.replace(minute=(when.minute // 30) * 30, second=0, microsecond=0)


def bucket_end(when: datetime) -> datetime:
    """Return the end of the current half-hour bucket."""

    start = bucket_start(when)
    return start + timedelta(minutes=30)


def find_period(
    periods: list[dict], when: datetime, direction: str, site_timezone: str = DEFAULT_SITE_TIMEZONE
) -> dict | None:
    """Find the TOU period matching a timestamp and direction."""

    minute = minutes_since_midnight(site_time(when, site_timezone))
    for period in periods:
        if period["direction"] != direction:
            continue
        if period["start_minute"] <= minute < period["end_minute"]:
            return period
    return None


def current_billing_window(today: date, billing_cycle: str, start_day: int, start_month: int) -> tuple[date, date]:
    """Return the current cycle, anchored to the configured billing day.

    Monthly plans use the billing day in the current local month, rolling back
    one month when today's date is before that day.  ``start_month`` remains the
    anchor month for quarterly plans.
    """

    billing_day = max(1, int(start_day))

    def month_date(year: int, month: int) -> date:
        return date(year, month, min(billing_day, calendar.monthrange(year, month)[1]))

    def shift_months(value: date, months: int) -> date:
        month_index = value.year * 12 + value.month - 1 + months
        year, month_index = divmod(month_index, 12)
        return month_date(year, month_index + 1)

    if billing_cycle == "quarterly":
        anchor = month_date(today.year, int(start_month))
        if today < anchor:
            anchor = shift_months(anchor, -12)
        while shift_months(anchor, 3) <= today:
            anchor = shift_months(anchor, 3)
    else:
        anchor = month_date(today.year, today.month)
        if today < anchor:
            anchor = shift_months(anchor, -1)
    return anchor, today


def aggregate_bill_lines(
    snapshot_rows: Iterable[dict], tou_periods: list[dict], site_timezone: str = DEFAULT_SITE_TIMEZONE
) -> list[dict]:
    """Convert half-hour telemetry rows into bill lines."""

    lines: list[dict] = []
    for row in snapshot_rows:
        captured = row["captured_at"]
        for direction, kwh_key in (("import", "grid_import_kwh"), ("export", "grid_export_kwh")):
            period = find_period(tou_periods, captured, direction, site_timezone)
            if not period:
                continue
            kwh = float(row.get(kwh_key, 0.0))
            amount = kwh * float(period["rate_cents_per_kwh"])
            if direction == "export":
                amount *= -1.0
            lines.append(
                {
                    "day": site_time(captured, site_timezone).date(),
                    "captured_at": captured,
                    "period_label": period["label"],
                    "direction": direction,
                    "kwh": kwh,
                    "rate_cents_per_kwh": float(period["rate_cents_per_kwh"]),
                    "amount_cents": amount,
                    "tou_period_id": period.get("id"),
                    "pricing_revision_id": row.get("pricing_revision_id"),
                    # Preserve the period's pricing mode until the display
                    # rollup.  Only export rows with a positive allowance are
                    # eligible for daily tiering.
                    "export_tier_kwh": float(period.get("export_tier_kwh", 0.0) or 0.0),
                    "export_tier_rate_cents_per_kwh": float(period.get("export_tier_rate_cents_per_kwh", 0.0) or 0.0),
                    "export_excess_rate_cents_per_kwh": float(period.get("export_excess_rate_cents_per_kwh", 0.0) or 0.0),
                }
            )
    return lines


def rollup_by_day_and_period(
    lines: Iterable[dict], site_timezone: str = DEFAULT_SITE_TIMEZONE
) -> list[dict]:
    """Group bill lines into display rows for the current bill breakdown."""

    totals: dict[tuple[date, str, str, int | None, int | None, float, float, float], dict] = defaultdict(lambda: {"kwh": 0.0, "amount_cents": 0.0, "rate_cents_per_kwh": 0.0})
    for line in lines:
        captured = line.get("captured_at")
        day = site_time(captured, site_timezone).date() if captured else line["day"]
        tier = (float(line.get("export_tier_kwh", 0.0) or 0.0), float(line.get("export_tier_rate_cents_per_kwh", 0.0) or 0.0), float(line.get("export_excess_rate_cents_per_kwh", 0.0) or 0.0))
        key = (day, line["period_label"], line["direction"], line.get("tou_period_id"), line.get("pricing_revision_id"), *tier)
        bucket = totals[key]
        bucket["kwh"] += float(line["kwh"])
        bucket["amount_cents"] += float(line["amount_cents"])
        bucket["rate_cents_per_kwh"] = float(line["rate_cents_per_kwh"])
    out = []
    for (day, period_label, direction, period_id, revision_id, tier_kwh, tier_rate, excess_rate), bucket in sorted(totals.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        out.append(
            {
                "day": day.isoformat(),
                "period_label": period_label,
                "direction": direction,
                "kwh": round(bucket["kwh"], 4),
                "rate_cents_per_kwh": round(bucket["rate_cents_per_kwh"], 3),
                "amount_cents": round(bucket["amount_cents"], 3),
                "tou_period_id": period_id,
                "pricing_revision_id": revision_id,
                "export_tier_kwh": tier_kwh,
                "export_tier_rate_cents_per_kwh": tier_rate,
                "export_excess_rate_cents_per_kwh": excess_rate,
            }
        )
    return out


def apply_daily_export_tier(
    lines: Iterable[dict],
    tier_kwh: float | None = None,
    tier_rate_cents_per_kwh: float | None = None,
    excess_rate_cents_per_kwh: float | None = None,
) -> list[dict]:
    """Price exports progressively across each site's local billing day.

    Export credits are allocated in chronological line order.  A line is split
    when it crosses the daily threshold so the displayed kWh and amounts remain
    auditable while the first threshold is applied only once per day.
    """
    # Optional arguments retain the public helper's legacy plan-wide mode.
    # Normal billing obtains the configuration from each export TOU period.
    legacy = None if tier_kwh is None else (tier_kwh, tier_rate_cents_per_kwh or 0.0, excess_rate_cents_per_kwh or 0.0)
    used_by_day: dict[tuple[date, int | None, str], float] = defaultdict(float)
    output: list[dict] = []
    for line in lines:
        if line.get("direction") != "export" or not line.get("kwh"):
            output.append(line)
            continue
        configured = legacy or (
            float(line.get("export_tier_kwh", 0.0) or 0.0),
            float(line.get("export_tier_rate_cents_per_kwh", 0.0) or 0.0),
            float(line.get("export_excess_rate_cents_per_kwh", 0.0) or 0.0),
        )
        configured_kwh, configured_tier_rate, configured_excess_rate = configured
        # A non-tiered export period is represented by zero/default tier
        # fields.  Treat an incomplete tier tuple the same way: preserve the
        # ordinary period rate and amount instead of repricing the line at 0c.
        # This is especially important for plans that have TOU export rates
        # but no daily export allowance.
        if not (
            configured_kwh > 0
            and configured_tier_rate > 0
            and configured_excess_rate > 0
        ):
            output.append(line)
            continue
        day = line["day"] if isinstance(line["day"], date) else date.fromisoformat(line["day"])
        remaining = float(line["kwh"])
        usage_key = (
            day,
            line.get("pricing_revision_id"),
            str(line.get("tou_period_id") or line.get("period_label", "")),
        )
        while remaining > 0.0000001:
            tier_remaining = max(0.0, configured_kwh - used_by_day[usage_key])
            quantity = min(remaining, tier_remaining) if tier_remaining else remaining
            rate = configured_tier_rate if tier_remaining else configured_excess_rate
            priced = dict(line)
            priced["day"] = day.isoformat() if not isinstance(line["day"], date) else line["day"]
            priced["kwh"] = round(quantity, 4)
            priced["rate_cents_per_kwh"] = rate
            priced["amount_cents"] = round(-quantity * rate, 3)
            suffix = "tier 1" if tier_remaining else "excess"
            label = line["period_label"]
            if "Meter reconciliation (flat " in label:
                label = label.replace("Meter reconciliation (flat ", "Meter reconciliation (tiered; base ")
            priced["period_label"] = f"{label} ({suffix})"
            output.append(priced)
            used_by_day[usage_key] += quantity
            remaining -= quantity
    return output
