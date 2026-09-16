"""FastAPI application, routes, and background polling loop."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.staticfiles import StaticFiles

from .config import RuntimeConfig
from .models import (
    BillSummaryResponse, ChartResponse, CloseDayResponse, DashboardStateResponse,
    MutationErrorResponse, MutationSuccessResponse, TouPeriodsResponse,
    WeatherRecommendationResponse,
)
from .service import PlanDeletionError, SolarmaxService


def _currency_to_cents(value: str) -> float:
    """Parse a user-entered currency amount and return cents."""
    cleaned = re.sub(r"[\s$]", "", value or "")
    if not cleaned:
        return 0.0
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        raise ValueError("Daily supply charge must be a currency amount such as $1.78")
    return round(float(cleaned) * 100, 2)

config = RuntimeConfig()
service = SolarmaxService(config.db_path)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logger = logging.getLogger(__name__)
WEBUI_INDEX = Path(__file__).parent / "static" / "webui" / "index.html"


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
    # A textarea submission may retain a line-ending control beside a pasted
    # time.  It is presentation whitespace, not part of the HH:MM value.
    value = value.strip()
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


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "").lower()


def _mutation_success(redirect_to: str, resource_id: int | None = None, data: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(MutationSuccessResponse(redirect_to=redirect_to, resource_id=resource_id, data=data).model_dump(mode="json"))


def _mutation_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=MutationErrorResponse(error={"code": code, "message": message}).model_dump(mode="json"),
    )


JSON_MUTATION_ERROR_RESPONSES = {
    404: {"model": MutationErrorResponse},
    422: {"model": MutationErrorResponse},
    502: {"model": MutationErrorResponse},
    504: {"model": MutationErrorResponse},
}
JSON_MUTATION_RESPONSES = {
    200: {"model": MutationSuccessResponse},
    **JSON_MUTATION_ERROR_RESPONSES,
}
PLAN_DELETE_RESPONSES = {
    200: {"model": MutationSuccessResponse},
    404: {"model": MutationErrorResponse},
    409: {"model": MutationErrorResponse},
}

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


@app.middleware("http")
async def no_store_api_responses(request: Request, call_next):
    """Prevent API state and mutation responses from being cached."""

    try:
        response = await call_next(request)
    except Exception:
        if request.url.path.startswith("/api/"):
            logger.exception("Unhandled API error")
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
        else:
            raise
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class SolarmaxStaticFiles(StaticFiles):
    """Keep legacy static serving while making fingerprinted web assets immutable."""

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("webui/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path == "webui/index.html" and response.status_code == 200:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.mount("/static", SolarmaxStaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def _serve_react_webui() -> bool:
    """Return true only for an explicit rollout and a complete built entrypoint."""

    if config.webui_mode != "react":
        return False
    if WEBUI_INDEX.is_file():
        return True
    logger.warning("React WebUI rollout requested but compiled entrypoint is unavailable; serving legacy UI")
    return False


def _page_response(request: Request, template_name: str, context: dict[str, Any]) -> Response:
    """Serve a static React entrypoint during rollout, otherwise preserve Jinja rendering."""

    if _serve_react_webui():
        return FileResponse(WEBUI_INDEX, media_type="text/html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return templates.TemplateResponse(request, template_name, context)


@app.exception_handler(RequestValidationError)
async def json_mutation_validation_error(request: Request, exc: RequestValidationError):
    """Use the documented mutation error envelope for negotiated JSON only."""

    if request.method == "POST" and request.url.path.startswith("/api/") and _wants_json(request):
        return _mutation_error(422, "validation_error", str(exc))
    return await request_validation_exception_handler(request, exc)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    state = service.dashboard_state()
    return _page_response(request, "dashboard.html", {"state": state, "chart_points": service.chart_points(), "bill": state["bill"], "settings": state["settings"]})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> Response:
    state = service.dashboard_state()
    return _page_response(request, "settings.html", {"state": state})


@app.get("/inverters", response_class=HTMLResponse)
def inverters_page(request: Request) -> Response:
    state = service.dashboard_state()
    return _page_response(request, "inverters.html", {"state": state, "inverters": service.list_inverters()})


@app.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request) -> Response:
    state = service.dashboard_state()
    plans = service.list_power_plans()
    active = service.load_app_settings().active_plan_id
    tou = {plan["id"]: _tou_for_editor(service.list_tou_periods(plan["id"])) for plan in plans}
    return _page_response(request, "plans.html", {"state": state, "plans": plans, "tou_by_plan": tou, "active_plan_id": active})


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request) -> Response:
    state = service.dashboard_state()
    bill = service.current_bill_summary()
    return _page_response(request, "billing.html", {"state": state, "bill": bill})


@app.get("/api/state", response_model=DashboardStateResponse)
def api_state() -> dict[str, Any]:
    return service.dashboard_state()


@app.get("/api/chart", response_model=ChartResponse)
def api_chart(days: int = 14) -> dict[str, Any]:
    if not 1 <= days <= 366:
        raise HTTPException(status_code=422, detail="days must be between 1 and 366")
    return {"points": service.chart_points(days=days)}


@app.get("/api/bill", response_model=BillSummaryResponse)
def api_bill() -> dict[str, Any]:
    return service.current_bill_summary()


@app.post("/api/close-day", response_model=CloseDayResponse)
def api_close_day() -> dict[str, Any]:
    return service.close_day()


@app.get("/api/plans/{plan_id}/tou", response_model=TouPeriodsResponse)
def api_tou_read(plan_id: int) -> dict[str, Any]:
    if not service.get_power_plan(plan_id):
        raise HTTPException(status_code=404, detail=f"No power plan with id {plan_id}")
    return {"plan_id": plan_id, "periods": service.list_tou_periods(plan_id)}


@app.post("/api/settings", responses=JSON_MUTATION_RESPONSES)
def api_settings(
    request: Request,
    theme: str = Form(...),
    mode: str = Form(...),
    site_name: str = Form(...),
    site_lat: float = Form(...),
    site_lon: float = Form(...),
    site_timezone: str = Form("Australia/Brisbane"),
    poll_interval_seconds: int = Form(...),
    active_plan_id: str = Form(""),
):
    try:
        service.save_app_settings({
            "theme": theme,
            "mode": mode,
            "site_name": site_name,
            "site_lat": site_lat,
            "site_lon": site_lon,
            "site_timezone": site_timezone,
            "poll_interval_seconds": poll_interval_seconds,
            "active_plan_id": int(active_plan_id) if active_plan_id else None,
        })
    except KeyError as exc:
        if _wants_json(request):
            return _mutation_error(404, "not_found", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        if _wants_json(request):
            return _mutation_error(422, "validation_error", str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _wants_json(request):
        return _mutation_success("/settings")
    return RedirectResponse("/settings", status_code=303)


@app.post("/api/inverters", responses=JSON_MUTATION_RESPONSES)
def api_inverters(
    request: Request,
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
    try:
        saved_id = service.upsert_inverter({
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
        })
    except KeyError as exc:
        if _wants_json(request):
            return _mutation_error(404, "not_found", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        if _wants_json(request):
            return _mutation_error(422, "validation_error", str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _wants_json(request):
        return _mutation_success("/inverters", saved_id)
    return RedirectResponse("/inverters", status_code=303)


@app.post("/api/plans", responses=JSON_MUTATION_RESPONSES)
def api_plans(
    request: Request,
    plan_id: int | None = Form(None),
    provider_name: str = Form(...),
    plan_name: str = Form(...),
    billing_cycle: str = Form(...),
    billing_start_day: int = Form(...),
    billing_start_month: int = Form(...),
    daily_supply_charge: str = Form("$0.00"),
    export_tier_kwh: float | None = Form(None),
    export_tier_rate_cents_per_kwh: float | None = Form(None),
    export_excess_rate_cents_per_kwh: float | None = Form(None),
    notes: str = Form(""),
):
    try:
        existing = service.get_power_plan(plan_id) if plan_id else None
        new_id = service.upsert_power_plan({
            "id": plan_id,
            "provider_name": provider_name,
            "plan_name": plan_name,
            "billing_cycle": billing_cycle,
            "billing_start_day": billing_start_day,
            "billing_start_month": billing_start_month,
            "daily_supply_charge_cents": _currency_to_cents(daily_supply_charge),
            "export_tier_kwh": export_tier_kwh if export_tier_kwh is not None else (existing or {}).get("export_tier_kwh", 0.0),
            "export_tier_rate_cents_per_kwh": export_tier_rate_cents_per_kwh if export_tier_rate_cents_per_kwh is not None else (existing or {}).get("export_tier_rate_cents_per_kwh", 0.0),
            "export_excess_rate_cents_per_kwh": export_excess_rate_cents_per_kwh if export_excess_rate_cents_per_kwh is not None else (existing or {}).get("export_excess_rate_cents_per_kwh", 0.0),
            "notes": notes,
        })
        settings = service.load_app_settings().model_dump()
        settings["active_plan_id"] = new_id
        service.save_app_settings(settings)
    except KeyError as exc:
        if _wants_json(request):
            return _mutation_error(404, "not_found", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        if _wants_json(request):
            return _mutation_error(422, "validation_error", str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _wants_json(request):
        return _mutation_success("/plans", new_id)
    return RedirectResponse("/plans", status_code=303)


@app.delete("/api/plans/{plan_id}", responses=PLAN_DELETE_RESPONSES)
def api_plan_delete(plan_id: int):
    try:
        service.delete_power_plan(plan_id)
    except KeyError as exc:
        return _mutation_error(404, "not_found", str(exc))
    except PlanDeletionError as exc:
        return _mutation_error(409, exc.code, exc.message)
    return _mutation_success("/plans", plan_id)


@app.post("/api/tou/{plan_id}", responses=JSON_MUTATION_RESPONSES)
def api_tou(request: Request, plan_id: int, payload: str = Form(...), daily_supply_charge: str = Form("$0.00"), export_tier_kwh: float | None = Form(None), export_tier_rate_cents_per_kwh: float | None = Form(None), export_excess_rate_cents_per_kwh: float | None = Form(None)):
    if not service.get_power_plan(plan_id):
        if _wants_json(request):
            return _mutation_error(404, "not_found", f"No power plan with id {plan_id}")
        raise HTTPException(status_code=404, detail=f"No power plan with id {plan_id}")
    try:
        # Tiers now belong to individual export periods in ``payload``.  Do
        # not silently attach the retired plan-wide fields to an arbitrary
        # period: partial legacy submissions could otherwise create a tier
        # with a zero credit rate.
        if any(value is not None for value in (
            export_tier_kwh,
            export_tier_rate_cents_per_kwh,
            export_excess_rate_cents_per_kwh,
        )):
            raise ValueError(
                "Plan-wide export tier fields are deprecated; configure all tier values on an export TOU period"
            )
        # Textareas submitted by browsers may contain raw line-ending control
        # characters inside an edited string.  Decode them here, then validate
        # each typed field below rather than rejecting the whole form first.
        periods = json.loads(payload, strict=False)
        if not isinstance(periods, list) or not all(isinstance(period, dict) for period in periods):
            raise ValueError("TOU payload must be a JSON array of periods")
        for period in periods:
            # DB identity and ownership are not editable fields.  In particular,
            # never pass a persisted plan_id alongside the route's plan_id.
            period.pop("id", None)
            period.pop("plan_id", None)
            period["start_minute"] = _time_to_minute(period.get("start_minute"))
            period["end_minute"] = _time_to_minute(period.get("end_minute"))
        supply_charge_cents = _currency_to_cents(daily_supply_charge)
        service.save_tou_schedule(plan_id, periods, supply_charge_cents)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        if _wants_json(request):
            return _mutation_error(422, "validation_error", str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _wants_json(request):
        return _mutation_success("/plans", plan_id)
    return RedirectResponse("/plans", status_code=303)


@app.post("/api/poll-now", responses=JSON_MUTATION_RESPONSES)
def api_poll_now(request: Request):
    service.poll_once()
    if _wants_json(request):
        return _mutation_success("/")
    return RedirectResponse("/", status_code=303)


@app.post(
    "/api/ai/recommend",
    response_model=WeatherRecommendationResponse | MutationSuccessResponse,
    responses=JSON_MUTATION_ERROR_RESPONSES,
)
def api_ai_recommend(request: Request, inverter_id: int | None = Form(None)):
    try:
        result = service.weather_and_recommendation(inverter_id)
    except KeyError as exc:
        if _wants_json(request):
            return _mutation_error(404, "not_found", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.TimeoutException:
        return _mutation_error(504, "weather_timeout", "Weather service timed out")
    except httpx.HTTPError:
        return _mutation_error(502, "weather_unavailable", "Weather service is unavailable")
    if _wants_json(request):
        return _mutation_success("/inverters", inverter_id, result)
    return result


@app.post("/api/ai/apply", responses=JSON_MUTATION_RESPONSES)
def api_ai_apply(request: Request, inverter_id: int = Form(...)):
    try:
        rec = service.weather_and_recommendation(inverter_id)["recommendation"]
        service.apply_recommendation(inverter_id, rec)
    except KeyError as exc:
        if _wants_json(request):
            return _mutation_error(404, "not_found", str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.TimeoutException:
        return _mutation_error(504, "weather_timeout", "Weather service timed out")
    except httpx.HTTPError:
        return _mutation_error(502, "weather_unavailable", "Weather service is unavailable")
    if _wants_json(request):
        return _mutation_success("/inverters", inverter_id, service.get_inverter(inverter_id))
    return RedirectResponse("/inverters", status_code=303)
