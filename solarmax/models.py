"""Pydantic data models used by the UI, API, and MCP server."""
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


ThemeName = Literal[
    "classic-light",
    "classic-dark",
    "deep-ocean",
    "ember-core",
]

ModeName = Literal["manual", "ai"]
BillingCycle = Literal["monthly", "quarterly"]
TouDirection = Literal["import", "export"]
AdapterKind = Literal["sigenstor_ec_20_0_tp_au"]


class AppSettings(BaseModel):
    """Application-wide preferences."""

    theme: ThemeName = "classic-dark"
    mode: ModeName = "manual"
    poll_interval_seconds: int = Field(default=30, ge=5, le=3600)
    site_name: str = "Solarmax"
    site_lat: float = -27.4698
    site_lon: float = 153.0251
    site_timezone: str = "Australia/Brisbane"
    active_plan_id: Optional[int] = None

    @field_validator("site_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            return "Australia/Brisbane"
        return value


class InverterProfile(BaseModel):
    """Editable inverter profile with future module extension points."""

    id: Optional[int] = None
    name: str
    model: str = "SigenStor EC 20.0 TP AU"
    adapter_kind: AdapterKind = "sigenstor_ec_20_0_tp_au"
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
    daily_supply_charge_cents: float = Field(default=0.0, ge=0.0, le=100000.0)
    export_tier_kwh: float = Field(default=0.0, ge=0.0, le=100000.0)
    export_tier_rate_cents_per_kwh: float = Field(default=0.0, ge=0.0, le=999.0)
    export_excess_rate_cents_per_kwh: float = Field(default=0.0, ge=0.0, le=999.0)
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
    export_tier_kwh: float = Field(default=0.0, ge=0.0, le=100000.0)
    export_tier_rate_cents_per_kwh: float = Field(default=0.0, ge=0.0, le=999.0)
    export_excess_rate_cents_per_kwh: float = Field(default=0.0, ge=0.0, le=999.0)

    @model_validator(mode="after")
    def export_tiers_match_direction(self):
        tiers = (
            self.export_tier_kwh,
            self.export_tier_rate_cents_per_kwh,
            self.export_excess_rate_cents_per_kwh,
        )
        if self.direction == "import" and any(tiers):
            raise ValueError("Import TOU periods cannot have export tier values")
        if any(tiers) and (self.export_tier_kwh <= 0 or self.export_tier_rate_cents_per_kwh <= 0 or self.export_excess_rate_cents_per_kwh <= 0):
            raise ValueError("Tiered export periods require allowance, tier-one rate, and excess rate")
        return self

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
    direction: Literal["import", "export", "fixed"]
    kwh: float
    rate_cents_per_kwh: float | None
    amount_cents: float | None
    unpriced: bool = False


class DailySiteTotal(BaseModel):
    """Authoritative local-day energy totals and the corresponding bill."""

    day: date
    solar_kwh: float
    load_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    daily_bill_amount_cents: float


# API response models intentionally describe the existing additive wire
# format.  Persistence models above remain the validation boundary for writes.
class LivePowerResponse(BaseModel):
    solar_kw: float
    load_kw: float
    grid_import_kw: float
    grid_export_kw: float
    battery_charge_kw: float
    battery_discharge_kw: float


class DailyEnergyTotalsResponse(BaseModel):
    solar_total_kwh: float
    load_total_kwh: float
    grid_import_total_kwh: float
    grid_export_total_kwh: float
    battery_charge_total_kwh: float
    battery_discharge_total_kwh: float


class InverterResponse(InverterProfile):
    reachable: bool = False


class PowerPlanResponse(PowerPlan):
    id: int


class TouPeriodResponse(TouPeriod):
    id: int


class TouPeriodsResponse(BaseModel):
    plan_id: int
    periods: list[TouPeriodResponse]


class BillSummaryResponse(BaseModel):
    plan: PowerPlanResponse | None
    total_cents: float
    rows: list[BillingLine] = Field(default_factory=list)
    daily: list[BillingLine] = Field(default_factory=list)
    daily_site_totals: list[DailySiteTotal] = Field(default_factory=list)
    today_grid_import_kwh: float
    today_grid_export_kwh: float
    supply_charge_cents: float
    supply_charge_days: int
    billing_window_applied: bool
    # Legacy sparse fields are retained during the migration window.
    lines: list[Any] = Field(default_factory=list, deprecated=True)
    rollups: list[Any] = Field(default_factory=list, deprecated=True)


class DashboardStateResponse(BaseModel):
    settings: AppSettings
    inverters: list[InverterResponse]
    power_plans: list[PowerPlanResponse]
    live: LivePowerResponse | None
    totals: DailyEnergyTotalsResponse | None
    live_observed_at: datetime | None
    all_reachable: bool
    bill: BillSummaryResponse
    theme: ThemeName


class ChartPointResponse(BaseModel):
    day: date
    amount_cents: float


class ChartResponse(BaseModel):
    points: list[ChartPointResponse]


class CloseDayResponse(BaseModel):
    day: date
    solar_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float


class WeatherRecommendationResponse(BaseModel):
    weather: dict[str, Any]
    recommendation: dict[str, Any]


class MutationSuccessResponse(BaseModel):
    ok: Literal[True] = True
    redirect_to: str
    resource_id: int | None = None
    data: dict[str, Any] | None = None


class MutationErrorDetail(BaseModel):
    code: str
    message: str


class MutationErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: MutationErrorDetail
