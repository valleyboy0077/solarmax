"""MCP server exposing Solarmax settings and control tools."""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import RuntimeConfig
from .service import SolarmaxService

config = RuntimeConfig()
service = SolarmaxService(config.db_path)
mcp = FastMCP("Solarmax")


@mcp.tool()
def get_dashboard_state() -> dict:
    """Return the current dashboard state."""

    return service.dashboard_state()


@mcp.tool()
def list_inverters() -> list[dict]:
    """List all inverter profiles."""

    return service.list_inverters()


@mcp.tool()
def update_inverter(inverter: dict) -> dict:
    """Upsert an inverter profile and return the saved record."""

    inverter_id = service.upsert_inverter(inverter)
    return service.get_inverter(inverter_id) or {}


@mcp.tool()
def list_power_plans() -> list[dict]:
    """List available power plans."""

    return service.list_power_plans()


@mcp.tool()
def set_app_settings(settings: dict) -> dict:
    """Persist app settings and return the updated values."""

    service.save_app_settings(settings)
    return service.load_app_settings().model_dump()


@mcp.tool()
def get_weather_and_recommendation() -> dict:
    """Fetch weather and produce a battery policy recommendation."""

    return service.weather_and_recommendation()


@mcp.tool()
def apply_recommendation(inverter_id: int, recommendation: dict) -> dict:
    """Apply a recommendation to the selected inverter profile."""

    service.apply_recommendation(inverter_id, recommendation)
    return service.get_inverter(inverter_id) or {}


if __name__ == "__main__":
    mcp.run()
