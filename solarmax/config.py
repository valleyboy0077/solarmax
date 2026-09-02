"""Central configuration helpers for Solarmax."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_DB_PATH = Path(os.environ.get("SOLARMAX_DB_PATH", "/data/solarmax.db"))
DEFAULT_THEME = "classic-dark"
DEFAULT_POLL_SECONDS = 30
DEFAULT_SITE_LAT = -27.4698
DEFAULT_SITE_LON = 153.0251


@dataclass(frozen=True)
class RuntimeConfig:
    """Container for runtime values that are convenient to pass around."""

    db_path: Path = DEFAULT_DB_PATH
    host: str = os.environ.get("SOLARMAX_HOST", "0.0.0.0")
    port: int = int(os.environ.get("SOLARMAX_PORT", "9117"))
