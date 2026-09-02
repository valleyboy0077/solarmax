"""FastAPI application, routes, and background polling loop."""
from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.staticfiles import StaticFiles

from .config import RuntimeConfig
from .service import SolarmaxService

config = RuntimeConfig()
service = SolarmaxService(config.db_path)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def format_au_date(value: str | date | datetime) -> str:
    """Format an ISO date for display without changing its API/storage form."""

    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")


def _minute_to_time(value: int) -> str:
    """Render minutes since midnight as a 24-hour time for the TOU editor."""

    hours, minutes = divmod(int(value), 60)
    return f"{hours:02d}:{minutes:02d}"


def _time_to_minute(value: Any) -> int:
    """Convert a 24-hour HH:MM editor value back to minutes since midnight."""

    if isinstance(value, int):  # Accept legacy numeric textarea payloads too.
        return value
    if not isinstance(value, str):
        raise ValueError("TOU times must use HH:MM format")
    if value == "24:00":
        return 24 * 60
    if len(value) != 5 or value[2] != ":" or not (value[:2] + value[3:]).isdigit():
        raise ValueError("TOU times must use 24-hour HH:MM format")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("TOU times must use 24-hour HH:MM format") from exc
    return parsed.hour * 60 + parsed.minute


def _tou_for_editor(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare persisted TOU rows for the human-readable JSON editor."""

    return [
        {
            **period,
            "start_minute": _minute_to_time(period["start_minute"]),
            "end_minute": _minute_to_time(period["end_minute"]),
        }
        for period in periods
    ]


templates.env.globals["format_au_date"] = format_au_date

_stop_event = threading.Event()
_poll_thread: threading.Thread | None = None


def _poll_loop() -> None:
    """Continuously poll the inverter set using the configured interval."""

    while not _stop_event.is_set():
        try:
            service.poll_once()
            interval = service.load_app_settings().poll_interval_seconds
        except Exception:
            interval = 30
        _stop_event.wait(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _poll_thread
    _stop_event.clear()
    if _poll_thread is None or not _poll_thread.is_alive():
        _poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        _poll_thread.start()
    yield
    _stop_event.set()


app = FastAPI(title="Solarmax", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    state = service.dashboard_state()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"state": state, "chart_points": service.chart_points(), "bill": state["bill"], "settings": state["settings"]},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    state = service.dashboard_state()
    return templates.TemplateResponse(request, "settings.html", {"state": state})


@app.get("/inverters", response_class=HTMLResponse)
def inverters_page(request: Request) -> HTMLResponse:
    state = service.dashboard_state()
    return templates.TemplateResponse(request, "inverters.html", {"state": state, "inverters": service.list_inverters()})


@app.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request) -> HTMLResponse:
    state = service.dashboard_state()
    plans = service.list_power_plans()
    active = service.load_app_settings().active_plan_id
    tou = {plan["id"]: _tou_for_editor(service.list_tou_periods(plan["id"])) for plan in plans}
    return templates.TemplateResponse(request, "plans.html", {"state": state, "plans": plans, "tou_by_plan": tou, "active_plan_id": active})


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request) -> HTMLResponse:
    state = service.dashboard_state()
    bill = service.current_bill_summary()
    return templates.TemplateResponse(request, "billing.html", {"state": state, "bill": bill})


@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(service.dashboard_state())


@app.get("/api/chart")
def api_chart(days: int = 14) -> JSONResponse:
    return JSONResponse({"points": service.chart_points(days=days)})


@app.get("/api/bill")
def api_bill() -> JSONResponse:
    return JSONResponse(service.current_bill_summary())


@app.post("/api/settings")
def api_settings(
    theme: str = Form(...),
    mode: str = Form(...),
    site_name: str = Form(...),
    site_lat: float = Form(...),
    site_lon: float = Form(...),
    poll_interval_seconds: int = Form(...),
    active_plan_id: str = Form(""),
):
    service.save_app_settings(
        {
            "theme": theme,
            "mode": mode,
            "site_name": site_name,
            "site_lat": site_lat,
            "site_lon": site_lon,
            "poll_interval_seconds": poll_interval_seconds,
            "active_plan_id": int(active_plan_id) if active_plan_id else None,
        }
    )
    return RedirectResponse("/settings", status_code=303)


@app.post("/api/inverters")
def api_inverters(
    inverter_id: int | None = Form(None),
    name: str = Form(...),
    model: str = Form(...),
    adapter_kind: str = Form(...),
    ip_address: str = Form(""),
    subnet: str = Form(""),
    enabled: bool = Form(False),
    battery_feed_in_limit_kw: float = Form(0.0),
    battery_reserve_percent: int = Form(20),
    export_limit_kw: float = Form(0.0),
    allow_grid_charge: bool = Form(False),
    notes: str = Form(""),
):
    service.upsert_inverter(
        {
            "id": inverter_id,
            "name": name,
            "model": model,
            "adapter_kind": adapter_kind,
            "ip_address": ip_address,
            "subnet": subnet,
            "enabled": enabled,
            "battery_feed_in_limit_kw": battery_feed_in_limit_kw,
            "battery_reserve_percent": battery_reserve_percent,
            "export_limit_kw": export_limit_kw,
            "allow_grid_charge": allow_grid_charge,
            "notes": notes,
        }
    )
    return RedirectResponse("/inverters", status_code=303)


@app.post("/api/plans")
def api_plans(
    plan_id: int | None = Form(None),
    provider_name: str = Form(...),
    plan_name: str = Form(...),
    billing_cycle: str = Form(...),
    billing_start_day: int = Form(...),
    billing_start_month: int = Form(...),
    notes: str = Form(""),
):
    new_id = service.upsert_power_plan(
        {
            "id": plan_id,
            "provider_name": provider_name,
            "plan_name": plan_name,
            "billing_cycle": billing_cycle,
            "billing_start_day": billing_start_day,
            "billing_start_month": billing_start_month,
            "notes": notes,
        }
    )
    settings = service.load_app_settings().model_dump()
    settings["active_plan_id"] = new_id
    service.save_app_settings(settings)
    return RedirectResponse("/plans", status_code=303)


@app.post("/api/tou/{plan_id}")
def api_tou(plan_id: int, payload: str = Form(...)):
    if not any(p["id"] == plan_id for p in service.list_power_plans()):
        raise HTTPException(status_code=404, detail=f"No power plan with id {plan_id}")
    try:
        periods = json.loads(payload)
        if not isinstance(periods, list) or not all(isinstance(period, dict) for period in periods):
            raise ValueError("TOU payload must be a JSON array of periods")
        for period in periods:
            # DB identity and ownership are not editable fields.  In particular,
            # never pass a persisted plan_id alongside the route's plan_id.
            period.pop("id", None)
            period.pop("plan_id", None)
            period["start_minute"] = _time_to_minute(period.get("start_minute"))
            period["end_minute"] = _time_to_minute(period.get("end_minute"))
        service.replace_tou_periods(plan_id, periods)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse("/plans", status_code=303)


@app.post("/api/poll-now")
def api_poll_now():
    service.poll_once()
    return RedirectResponse("/", status_code=303)


@app.post("/api/ai/recommend")
def api_ai_recommend():
    return JSONResponse(service.weather_and_recommendation())


@app.post("/api/ai/apply")
def api_ai_apply(inverter_id: int = Form(...)):
    rec = service.weather_and_recommendation()["recommendation"]
    service.apply_recommendation(inverter_id, rec)
    return RedirectResponse("/inverters", status_code=303)
