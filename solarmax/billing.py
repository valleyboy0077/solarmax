"""Billing and TOU calculation helpers."""
from __future__ import annotations

from collections import defaultdict
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
    """Approximate the current billing window for monthly or quarterly plans."""

    year = today.year
    anchor = date(year, start_month, min(start_day, 28))
    if today < anchor:
        anchor = date(year - 1, start_month, min(start_day, 28))
    if billing_cycle == "quarterly":
        while anchor <= today - timedelta(days=92):
            anchor = date(anchor.year, ((anchor.month - 1 + 3) % 12) + 1, min(start_day, 28))
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
                }
            )
    return lines


def rollup_by_day_and_period(
    lines: Iterable[dict], site_timezone: str = DEFAULT_SITE_TIMEZONE
) -> list[dict]:
    """Group bill lines into display rows for the current bill breakdown."""

    totals: dict[tuple[date, str, str], dict] = defaultdict(lambda: {"kwh": 0.0, "amount_cents": 0.0, "rate_cents_per_kwh": 0.0})
    for line in lines:
        captured = line.get("captured_at")
        day = site_time(captured, site_timezone).date() if captured else line["day"]
        key = (day, line["period_label"], line["direction"])
        bucket = totals[key]
        bucket["kwh"] += float(line["kwh"])
        bucket["amount_cents"] += float(line["amount_cents"])
        bucket["rate_cents_per_kwh"] = float(line["rate_cents_per_kwh"])
    out = []
    for (day, period_label, direction), bucket in sorted(totals.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        out.append(
            {
                "day": day.isoformat(),
                "period_label": period_label,
                "direction": direction,
                "kwh": round(bucket["kwh"], 4),
                "rate_cents_per_kwh": round(bucket["rate_cents_per_kwh"], 3),
                "amount_cents": round(bucket["amount_cents"], 3),
            }
        )
    return out


def apply_daily_export_tier(
    lines: Iterable[dict],
    tier_kwh: float,
    tier_rate_cents_per_kwh: float,
    excess_rate_cents_per_kwh: float,
) -> list[dict]:
    """Price exports progressively across each site's local billing day.

    Export credits are allocated in chronological line order.  A line is split
    when it crosses the daily threshold so the displayed kWh and amounts remain
    auditable while the first threshold is applied only once per day.
    """
    if tier_kwh <= 0 or tier_rate_cents_per_kwh <= 0 or excess_rate_cents_per_kwh <= 0:
        return list(lines)
    used_by_day: dict[date, float] = defaultdict(float)
    output: list[dict] = []
    for line in lines:
        if line.get("direction") != "export" or not line.get("kwh"):
            output.append(line)
            continue
        day = line["day"] if isinstance(line["day"], date) else date.fromisoformat(line["day"])
        remaining = float(line["kwh"])
        while remaining > 0.0000001:
            tier_remaining = max(0.0, tier_kwh - used_by_day[day])
            quantity = min(remaining, tier_remaining) if tier_remaining else remaining
            rate = tier_rate_cents_per_kwh if tier_remaining else excess_rate_cents_per_kwh
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
            used_by_day[day] += quantity
            remaining -= quantity
    return output
