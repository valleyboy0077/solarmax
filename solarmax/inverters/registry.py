"""Adapter registry for future inverter module expansion."""
from __future__ import annotations

from .base import InverterAdapter
from .sigenstor_ec_20_0_tp_au import SigenStorEC20TPAUAdapter

ADAPTERS: dict[str, InverterAdapter] = {
    SigenStorEC20TPAUAdapter.kind: SigenStorEC20TPAUAdapter(),
}


def get_adapter(kind: str) -> InverterAdapter:
    """Return an adapter instance by profile kind, falling back to simulation."""

    return ADAPTERS.get(kind, ADAPTERS[SigenStorEC20TPAUAdapter.kind])
