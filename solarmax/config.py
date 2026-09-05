"""Central configuration helpers for Solarmax."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = Path(os.environ.get("SOLARMAX_DB_PATH", str(PROJECT_ROOT / "data" / "solarmax.db")))
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
    # The conservative default preserves the Jinja interface until rollout is explicitly enabled.
    webui_mode: str = os.environ.get("SOLARMAX_WEBUI_MODE", "legacy")
