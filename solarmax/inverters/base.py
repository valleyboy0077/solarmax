"""Inverter adapter interface and shared helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


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
        """Compute energy deltas against the previous total counters.

        Deltas are clamped to >= 0 so a counter reset (e.g. after the app was
        down and totals were re-baselined) never produces a negative energy
        value.
        """

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
    """Base class for future inverter modules.

    Adapters must return real hardware data only. When the inverter cannot be
    reached, ``read`` returns None — never fabricated or simulated values. The
    service layer records the unreachable state and the UI shows "—" for all
    values until a real reading succeeds again.
    """

    kind = "base"

    def read(self, profile: dict, previous: InverterReading | None) -> InverterReading | None:
        """Return a real reading, or None when the inverter is unreachable."""

        raise NotImplementedError

    def apply_profile(self, profile: dict, updates: dict) -> dict:
        """Apply control settings in a future hardware-specific implementation."""

        merged = dict(profile)
        merged.update(updates)
        return merged
