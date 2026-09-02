"""Inverter adapter interface and shared helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sin, pi
from random import Random


@dataclass
class InverterReading:
    """Normalized reading returned by any inverter adapter."""

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

    def deltas_from(self, previous: "InverterReading | None") -> dict[str, float]:
        """Compute energy deltas against the previous total counters."""

        if not previous:
            return {
                "delta_solar_kwh": 0.0,
                "delta_load_kwh": 0.0,
                "delta_grid_import_kwh": 0.0,
                "delta_grid_export_kwh": 0.0,
                "delta_battery_charge_kwh": 0.0,
                "delta_battery_discharge_kwh": 0.0,
            }
        return {
            "delta_solar_kwh": max(0.0, self.solar_total_kwh - previous.solar_total_kwh),
            "delta_load_kwh": max(0.0, self.load_total_kwh - previous.load_total_kwh),
            "delta_grid_import_kwh": max(0.0, self.grid_import_total_kwh - previous.grid_import_total_kwh),
            "delta_grid_export_kwh": max(0.0, self.grid_export_total_kwh - previous.grid_export_total_kwh),
            "delta_battery_charge_kwh": max(0.0, self.battery_charge_total_kwh - previous.battery_charge_total_kwh),
            "delta_battery_discharge_kwh": max(0.0, self.battery_discharge_total_kwh - previous.battery_discharge_total_kwh),
        }


class InverterAdapter:
    """Base class for future inverter modules."""

    kind = "base"

    def read(self, profile: dict, previous: InverterReading | None) -> InverterReading:
        raise NotImplementedError

    def apply_profile(self, profile: dict, updates: dict) -> dict:
        """Apply control settings in a future hardware-specific implementation."""

        merged = dict(profile)
        merged.update(updates)
        return merged


class SimulatedPowerCurve:
    """Deterministic signal generator that gives the UI realistic movement."""

    def __init__(self, inverter_id: int):
        self.rng = Random(inverter_id * 982451653)
        self.phase = self.rng.random() * 2 * pi

    def reading(self, profile: dict, previous: InverterReading | None) -> InverterReading:
        now = datetime.now(timezone.utc)
        minute_of_day = now.hour * 60 + now.minute + now.second / 60.0
        solar_shape = max(0.0, sin(((minute_of_day - 360) / 720.0) * pi))
        solar_kw = round(0.2 + solar_shape * 8.5 + self.rng.uniform(-0.2, 0.2), 3)
        load_kw = round(1.5 + 1.2 * sin(((minute_of_day - 180) / 1440.0) * 2 * pi) + self.rng.uniform(0.0, 0.8), 3)
        battery_charge_kw = round(max(0.0, solar_kw - load_kw - self.rng.uniform(0.0, 0.3)), 3)
        battery_discharge_kw = round(max(0.0, load_kw - solar_kw - self.rng.uniform(0.0, 0.3)), 3)
        grid_import_kw = round(max(0.0, load_kw - solar_kw - battery_discharge_kw), 3)
        grid_export_kw = round(max(0.0, solar_kw - load_kw - battery_charge_kw), 3)
        last = previous or InverterReading(
            captured_at=now,
            solar_kw=0.0,
            load_kw=0.0,
            grid_import_kw=0.0,
            grid_export_kw=0.0,
            battery_charge_kw=0.0,
            battery_discharge_kw=0.0,
            solar_total_kwh=0.0,
            load_total_kwh=0.0,
            grid_import_total_kwh=0.0,
            grid_export_total_kwh=0.0,
            battery_charge_total_kwh=0.0,
            battery_discharge_total_kwh=0.0,
        )
        step_hours = 30.0 / 3600.0
        solar_total_kwh = round(last.solar_total_kwh + solar_kw * step_hours, 5)
        load_total_kwh = round(last.load_total_kwh + load_kw * step_hours, 5)
        grid_import_total_kwh = round(last.grid_import_total_kwh + grid_import_kw * step_hours, 5)
        grid_export_total_kwh = round(last.grid_export_total_kwh + grid_export_kw * step_hours, 5)
        battery_charge_total_kwh = round(last.battery_charge_total_kwh + battery_charge_kw * step_hours, 5)
        battery_discharge_total_kwh = round(last.battery_discharge_total_kwh + battery_discharge_kw * step_hours, 5)
        return InverterReading(
            captured_at=now,
            solar_kw=solar_kw,
            load_kw=load_kw,
            grid_import_kw=grid_import_kw,
            grid_export_kw=grid_export_kw,
            battery_charge_kw=battery_charge_kw,
            battery_discharge_kw=battery_discharge_kw,
            solar_total_kwh=solar_total_kwh,
            load_total_kwh=load_total_kwh,
            grid_import_total_kwh=grid_import_total_kwh,
            grid_export_total_kwh=grid_export_total_kwh,
            battery_charge_total_kwh=battery_charge_total_kwh,
            battery_discharge_total_kwh=battery_discharge_total_kwh,
        )
