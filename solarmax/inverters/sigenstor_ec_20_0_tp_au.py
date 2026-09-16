"""SigenStor EC 20.0 TP AU profile module.

This adapter reads live telemetry from a real SigenStor inverter over Modbus
TCP using the verified Sigenergy register map (see the energy-system-modbus
skill reference). It returns real hardware data only — when the inverter is
unreachable it returns None so the UI can show "—" and an unreachable message.

Design notes:
- Live instantaneous kW is read straight from the inverter, so the dashboard
  shows real values (e.g. zero solar at night).
- Cumulative kWh totals come from the live Sigenergy lifetime U64 counters.
  They are authoritative on this hardware and avoid the drift inherent in
  integrating instantaneous power between polls.
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
    decode_u32,
    decode_u64,
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

# (preferred register address, normalized total key). All counters are U64,
# four big-endian registers, with a /100 gain to kWh.
_LIFETIME_READS = [
    (30088, "solar_total_kwh"),
    (30094, "load_total_kwh"),
    (30260, "grid_import_total_kwh"),
    (30264, "grid_export_total_kwh"),
    (30200, "battery_charge_total_kwh"),
    (30204, "battery_discharge_total_kwh"),
]

# Prefer the alternative plant grid lifetime registers when available. Keep
# the original plant-meter registers as a per-counter fallback for older
# firmware/register maps.
_LIFETIME_FALLBACKS = {
    "grid_import_total_kwh": (PLANT_UNIT_ID, 30216),
    "grid_export_total_kwh": (PLANT_UNIT_ID, 30220),
}

# Direct local-day registers are more useful than a newly-created lifetime
# baseline for the metrics the device publishes this way. Prefer the validated
# inverter daily PV register and retain the plant daily PV register as its
# fallback. Keep plant daily load 247:30092: it is the validated plant-load
# counter, while 247:30128 reports zero on this installation and must not be
# used. Grid import and export have no equivalent direct-day register, so those
# remain local-midnight deltas of the lifetime counters.
_DAILY_READS = [
    (1, 31509, "solar"),
    (PLANT_UNIT_ID, 30092, "load"),
    (1, 30566, "battery_charge"),
    (1, 30572, "battery_discharge"),
]

_DAILY_FALLBACKS = {
    "solar": (PLANT_UNIT_ID, 30272),
}

# Battery SOC and EMS mode are read for logging / future use but not stored in
# the normalized reading yet.
SOC_REGISTER = 30014          # plant_ess_soc, U16 /10
EMS_MODE_REGISTER = 30003     # plant_ems_work_mode, U16


def _read_registers_with_fallback(
    ip_address: str,
    unit_id: int,
    address: int,
    quantity: int,
    fallback: tuple[int, int] | None = None,
) -> list[int]:
    """Read a preferred register and retry a fallback only if unavailable.

    A zero is a valid counter value, so fallback selection is based on a
    Modbus request failure rather than on the returned value.
    """

    candidates = [(unit_id, address)]
    if fallback is not None:
        candidates.append(fallback)

    for index, (candidate_unit_id, candidate_address) in enumerate(candidates):
        try:
            return read_input_registers(ip_address, candidate_unit_id, candidate_address, quantity)
        except ModbusError as exc:
            if index == len(candidates) - 1:
                raise
            logger.info(
                "SigenStor %s register %s:%s unavailable: %s; trying fallback %s:%s",
                ip_address, candidate_unit_id, candidate_address, exc,
                candidates[index + 1][0], candidates[index + 1][1],
            )

    raise AssertionError("register candidates must not be empty")


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
        """Read live power, lifetime totals, and available daily registers."""

        # Read each instantaneous power register. Each is a signed 32-bit value
        # spanning two registers, scaled by /1000 to kW.
        power_kw: dict[str, float] = {}
        for address, key in _POWER_READS:
            regs = read_input_registers(ip_address, PLANT_UNIT_ID, address, 2)
            power_kw[key] = decode_s32(regs) / 1000.0

        totals_kwh: dict[str, float] = {}
        for address, key in _LIFETIME_READS:
            totals_kwh[key] = decode_u64(
                _read_registers_with_fallback(
                    ip_address,
                    PLANT_UNIT_ID,
                    address,
                    4,
                    _LIFETIME_FALLBACKS.get(key),
                )
            ) / 100.0

        daily_totals_kwh: dict[str, float] = {}
        for unit_id, address, key in _DAILY_READS:
            try:
                daily_totals_kwh[key] = decode_u32(
                    _read_registers_with_fallback(
                        ip_address, unit_id, address, 2, _DAILY_FALLBACKS.get(key),
                    )
                ) / 100.0
            except ModbusError as exc:
                # A firmware/register-map difference must not make otherwise
                # valid live and lifetime telemetry disappear. The service
                # falls back to the matching persisted counter source.
                logger.info(
                    "SigenStor %s daily %s register unavailable: %s",
                    ip_address, key, exc,
                )

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

        return InverterReading(
            captured_at=datetime.now(timezone.utc),
            solar_kw=round(power_kw["solar_kw"], 3),
            load_kw=round(power_kw["load_kw"], 3),
            grid_import_kw=round(grid_import_kw, 3),
            grid_export_kw=round(grid_export_kw, 3),
            battery_charge_kw=round(battery_charge_kw, 3),
            battery_discharge_kw=round(battery_discharge_kw, 3),
            **totals_kwh,
            lifetime=True,
            daily_totals_kwh=daily_totals_kwh,
        )
