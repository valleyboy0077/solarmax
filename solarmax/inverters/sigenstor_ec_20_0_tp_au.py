"""SigenStor EC 20.0 TP AU profile module."""
from __future__ import annotations

from .base import InverterAdapter, InverterReading, SimulatedPowerCurve


class SigenStorEC20TPAUAdapter(InverterAdapter):
    """First-version adapter for the requested inverter profile.

    This version is simulation-backed so the web UI and billing engine are
    usable immediately. The class exists as the extension point for a future
    real hardware integration without changing the app's outer shape.
    """

    kind = "sigenstor_ec_20_0_tp_au"

    def __init__(self):
        self._curves: dict[int, SimulatedPowerCurve] = {}

    def read(self, profile: dict, previous: InverterReading | None) -> InverterReading:
        inverter_id = int(profile["id"])
        curve = self._curves.setdefault(inverter_id, SimulatedPowerCurve(inverter_id))
        return curve.reading(profile, previous)
