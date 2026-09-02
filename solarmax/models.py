"""Pydantic data models used by the UI, API, and MCP server."""
from __future__ import annotations

from datetime import datetime, date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


ThemeName = Literal[
    "classic-light",
    "classic-dark",
    "solar-glass",
    "midnight-neon",
    "warm-desert",
]

ModeName = Literal["manual", "ai"]
BillingCycle = Literal["monthly", "quarterly"]
TouDirection = Literal["import", "export"]


class AppSettings(BaseModel):
    """Application-wide preferences."""

    theme: ThemeName = "classic-dark"
    mode: ModeName = "manual"
    poll_interval_seconds: int = Field(default=30, ge=5, le=3600)
    site_name: str = "Solarmax"
    site_lat: float = -27.4698
    site_lon: float = 153.0251
    active_plan_id: Optional[int] = None


class InverterProfile(BaseModel):
    """Editable inverter profile with future module extension points."""

    id: Optional[int] = None
    name: str
    model: str = "SigenStor EC 20.0 TP AU"
    adapter_kind: str = "sigenstor_ec_20_0_tp_au"
    ip_address: str = ""
    subnet: str = ""
    enabled: bool = True
    battery_feed_in_limit_kw: float = Field(default=0.0, ge=0.0, le=100.0)
    battery_reserve_percent: int = Field(default=20, ge=0, le=100)
    export_limit_kw: float = Field(default=0.0, ge=0.0, le=100.0)
    allow_grid_charge: bool = False
    notes: str = ""


class PowerPlan(BaseModel):
    """Billing plan and provider metadata."""

    id: Optional[int] = None
    provider_name: str
    plan_name: str
    billing_cycle: BillingCycle = "monthly"
    billing_start_day: int = Field(default=1, ge=1, le=31)
    billing_start_month: int = Field(default=1, ge=1, le=12)
    notes: str = ""


class TouPeriod(BaseModel):
    """Half-hour aligned TOU bracket."""

    id: Optional[int] = None
    plan_id: int
    direction: TouDirection
    label: str
    start_minute: int = Field(ge=0, le=24 * 60)
    end_minute: int = Field(ge=0, le=24 * 60)
    rate_cents_per_kwh: float = Field(ge=0.0, le=999.0)

    @field_validator("end_minute")
    @classmethod
    def end_must_follow_start(cls, end_minute: int, info):
        start = info.data.get("start_minute", 0)
        if end_minute <= start:
            raise ValueError("end_minute must be after start_minute")
        if start % 30 != 0 or end_minute % 30 != 0:
            raise ValueError("TOU periods must align to 30-minute blocks")
        return end_minute


class LiveMetric(BaseModel):
    """Snapshot of live power values."""

    inverter_id: int
    captured_at: datetime
    solar_kw: float
    load_kw: float
    grid_import_kw: float
    grid_export_kw: float
    battery_charge_kw: float
    battery_discharge_kw: float
    solar_total_kwh: float
    load_total_kwh: float
    grid_import_total_kwh: float
    grid_export_total_kwh: float
    battery_charge_total_kwh: float
    battery_discharge_total_kwh: float
    delta_solar_kwh: float
    delta_load_kwh: float
    delta_grid_import_kwh: float
    delta_grid_export_kwh: float
    delta_battery_charge_kwh: float
    delta_battery_discharge_kwh: float


class BillingLine(BaseModel):
    """Display-friendly billing breakdown entry."""

    day: date
    period_label: str
    direction: TouDirection
    kwh: float
    rate_cents_per_kwh: float
    amount_cents: float
