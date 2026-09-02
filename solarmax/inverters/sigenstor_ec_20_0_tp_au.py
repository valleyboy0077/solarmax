"""SigenStor EC 20.0 TP AU profile module.

This adapter reads live telemetry from a real SigenStor inverter over Modbus
TCP using the verified Sigenergy register map (see the energy-system-modbus
skill reference). It returns real hardware data only — when the inverter is
unreachable it returns None so the UI can show "—" and an unreachable message.

Design notes:
- Live instantaneous kW is read straight from the inverter, so the dashboard
  shows real values (e.g. zero solar at night).
- Cumulative kWh totals are integrated from the instantaneous power over the
  elapsed time since the previous reading. This is robust because the
  Sigenergy lifetime U64 counters are not populated on this hardware, while
  integrating kW over time is accurate at a 30-second poll interval and keeps
  the existing delta-from-totals logic working unchanged.
- If no IP is configured, or a Modbus read fails (inverter offline), the
  adapter returns None. The service records the unreachable state and the UI
  shows "—" for all values until a real reading succeeds again. No simulated
  data is ever produced.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import InverterAdapter, InverterReading
from .modbus import (
    ModbusError,
    decode_s32,
    read_input_registers,
)

logger = logging.getLogger(__name__)

# Sigenergy plant/EMS slave id and the input-register addresses for the
# instantaneous power values we need. Power registers are signed 32-bit with a
# gain of /1000 to kW. Sign conventions (verified against the vendor app):
#   grid  > 0 = importing from grid, < 0 = exporting to grid
#   ess   > 0 = charging battery,    < 0 = discharging battery
PLANT_UNIT_ID = 247

# (register address, key) for the instantaneous power reads.
_POWER_READS = [
    (30035, "solar_kw"),        # plant_sigen_photovoltaic_power
    (30284, "load_kw"),         # plant_total_load_power
    (30005, "grid_kw"),         # plant_grid_sensor_active_power (signed)
    (30037, "battery_kw"),      # plant_ess_power (signed)
]

# Battery SOC and EMS mode are read for logging / future use but not stored in
# the normalized reading yet.
SOC_REGISTER = 30014          # plant_ess_soc, U16 /10
EMS_MODE_REGISTER = 30003     # plant_ems_work_mode, U16


class SigenStorEC20TPAUAdapter(InverterAdapter):
    """Real-hardware adapter for the SigenStor EC 20.0 TP AU inverter."""

    kind = "sigenstor_ec_20_0_tp_au"

    def read(self, profile: dict, previous: InverterReading | None) -> InverterReading | None:
        """Read live power from the inverter, or return None if unreachable.

        No IP configured -> None (nothing to read). Modbus failure -> None
        with a logged warning. Never returns simulated data.
        """

        ip_address = (profile.get("ip_address") or "").strip()
        if not ip_address:
            logger.warning(
                "SigenStor %s has no IP address configured; returning None",
                profile.get("name"),
            )
            return None

        try:
            return self._read_real(ip_address, previous)
        except ModbusError as exc:
            logger.warning(
                "SigenStor %s (%s) Modbus read failed: %s",
                profile.get("name"), ip_address, exc,
            )
            return None

    def _read_real(self, ip_address: str, previous: InverterReading | None) -> InverterReading:
        """Read instantaneous power from the inverter and integrate totals."""

        # Read each instantaneous power register. Each is a signed 32-bit value
        # spanning two registers, scaled by /1000 to kW.
        power_kw: dict[str, float] = {}
        for address, key in _POWER_READS:
            regs = read_input_registers(ip_address, PLANT_UNIT_ID, address, 2)
            power_kw[key] = decode_s32(regs) / 1000.0

        # Read SOC and EMS mode for logging (not stored in the reading yet).
        try:
            soc_pct = read_input_registers(ip_address, PLANT_UNIT_ID, SOC_REGISTER, 1)[0] / 10.0
            ems_mode = read_input_registers(ip_address, PLANT_UNIT_ID, EMS_MODE_REGISTER, 1)[0]
            logger.info(
                "SigenStor %s live: solar=%.3f kW load=%.3f kW grid=%+.3f kW battery=%+.3f kW soc=%.1f%% mode=%d",
                ip_address, power_kw["solar_kw"], power_kw["load_kw"],
                power_kw["grid_kw"], power_kw["battery_kw"], soc_pct, ems_mode,
            )
        except ModbusError:
            # SOC/mode are non-critical; continue with the power values.
            pass

        # Split signed grid and battery power into import/export and
        # charge/discharge components.
        grid_kw = power_kw["grid_kw"]
        battery_kw = power_kw["battery_kw"]
        grid_import_kw = max(0.0, grid_kw)          # > 0 means importing
        grid_export_kw = max(0.0, -grid_kw)         # < 0 means exporting
        battery_charge_kw = max(0.0, battery_kw)    # > 0 means charging
        battery_discharge_kw = max(0.0, -battery_kw)  # < 0 means discharging

        now = datetime.now(timezone.utc)
        last = previous or InverterReading(
            captured_at=now,
            solar_kw=0.0, load_kw=0.0, grid_import_kw=0.0, grid_export_kw=0.0,
            battery_charge_kw=0.0, battery_discharge_kw=0.0,
            solar_total_kwh=0.0, load_total_kwh=0.0, grid_import_total_kwh=0.0,
            grid_export_total_kwh=0.0, battery_charge_total_kwh=0.0,
            battery_discharge_total_kwh=0.0,
        )

        # Elapsed time since the previous reading. If the gap is large (the app
        # was down, or this is the first reading after a long pause), we cannot
        # trust integrating over that whole span, so we re-baseline the running
        # totals to zero and start fresh. A small gap (normal polling) is
        # integrated as usual. The threshold sits well above the 30-second poll
        # interval so normal operation never triggers a reset.
        elapsed_seconds = (now - last.captured_at).total_seconds()
        if elapsed_seconds > 300.0:  # > 5 minutes -> treat as a fresh start
            hours = elapsed_seconds / 3600.0
            solar_total_kwh = round(power_kw["solar_kw"] * hours, 5)
            load_total_kwh = round(power_kw["load_kw"] * hours, 5)
            grid_import_total_kwh = round(grid_import_kw * hours, 5)
            grid_export_total_kwh = round(grid_export_kw * hours, 5)
            battery_charge_total_kwh = round(battery_charge_kw * hours, 5)
            battery_discharge_total_kwh = round(battery_discharge_kw * hours, 5)
        else:
            elapsed_hours = max(0.0, min(elapsed_seconds / 3600.0, 1.0))
            solar_total_kwh = round(last.solar_total_kwh + power_kw["solar_kw"] * elapsed_hours, 5)
            load_total_kwh = round(last.load_total_kwh + power_kw["load_kw"] * elapsed_hours, 5)
            grid_import_total_kwh = round(last.grid_import_total_kwh + grid_import_kw * elapsed_hours, 5)
            grid_export_total_kwh = round(last.grid_export_total_kwh + grid_export_kw * elapsed_hours, 5)
            battery_charge_total_kwh = round(last.battery_charge_total_kwh + battery_charge_kw * elapsed_hours, 5)
            battery_discharge_total_kwh = round(last.battery_discharge_total_kwh + battery_discharge_kw * elapsed_hours, 5)

        return InverterReading(
            captured_at=now,
            solar_kw=round(power_kw["solar_kw"], 3),
            load_kw=round(power_kw["load_kw"], 3),
            grid_import_kw=round(grid_import_kw, 3),
            grid_export_kw=round(grid_export_kw, 3),
            battery_charge_kw=round(battery_charge_kw, 3),
            battery_discharge_kw=round(battery_discharge_kw, 3),
            solar_total_kwh=solar_total_kwh,
            load_total_kwh=load_total_kwh,
            grid_import_total_kwh=grid_import_total_kwh,
            grid_export_total_kwh=grid_export_total_kwh,
            battery_charge_total_kwh=battery_charge_total_kwh,
            battery_discharge_total_kwh=battery_discharge_total_kwh,
        )
