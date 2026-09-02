"""Weather fetch and recommendation helpers for AI mode."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class WeatherSummary:
    """Small weather summary used by the recommendation engine."""

    source: str
    description: str
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    rain_mm: float | None = None
    cloud_cover_pct: float | None = None


def fetch_open_meteo(lat: float, lon: float) -> WeatherSummary:
    """Fetch a simple forecast from Open-Meteo without requiring an API key."""

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,cloud_cover_mean"
        "&forecast_days=1&timezone=auto"
    )
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    daily = payload.get("daily", {})
    description = "No forecast available"
    if daily:
        description = "Open-Meteo daily forecast loaded"
    return WeatherSummary(
        source="open-meteo",
        description=description,
        min_temp_c=(daily.get("temperature_2m_min") or [None])[0],
        max_temp_c=(daily.get("temperature_2m_max") or [None])[0],
        rain_mm=(daily.get("precipitation_sum") or [None])[0],
        cloud_cover_pct=(daily.get("cloud_cover_mean") or [None])[0],
    )


def recommend_battery_policy(weather: WeatherSummary | None, current_reserve: int, feed_in_limit_kw: float) -> dict[str, Any]:
    """Very small rules engine to propose battery settings for AI mode."""

    reserve = current_reserve
    feed_in = feed_in_limit_kw
    explanation = ["Baseline settings retained."]

    if weather:
        if weather.rain_mm is not None and weather.rain_mm > 5:
            reserve = min(80, max(reserve, 60))
            feed_in = min(feed_in, 0.5 if feed_in else 0.5)
            explanation.append("Rain expected: keep more battery reserve for evening load.")
        elif weather.cloud_cover_pct is not None and weather.cloud_cover_pct < 30:
            reserve = max(10, reserve - 10)
            feed_in = max(feed_in, 2.0)
            explanation.append("Clear sky: permit more export and a lower reserve.")
        elif weather.max_temp_c is not None and weather.max_temp_c > 33:
            reserve = max(reserve, 35)
            explanation.append("Hot day: keep a larger reserve for afternoon cooling load.")

    return {
        "recommended_reserve_percent": reserve,
        "recommended_feed_in_limit_kw": feed_in,
        "explanation": " ".join(explanation),
        "weather_source": weather.source if weather else None,
    }
