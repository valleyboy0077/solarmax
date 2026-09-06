"""Phase 0 API, provenance, and compatibility contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import types

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from solarmax.db import db_session
from solarmax.main import _tou_for_editor, app
from solarmax.service import SolarmaxService
from solarmax.weather import WeatherSummary


@pytest.fixture
def service(tmp_path, monkeypatch):
    instance = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", instance)
    return instance


def _telemetry(conn, inverter_id, captured_at):
    conn.execute(
        """INSERT INTO telemetry_raw
           (inverter_id, captured_at, solar_kw, load_kw, grid_import_kw,
            grid_export_kw, battery_charge_kw, battery_discharge_kw,
            solar_total_kwh, load_total_kwh, grid_import_total_kwh,
            grid_export_total_kwh, battery_charge_total_kwh,
            battery_discharge_total_kwh, delta_solar_kwh, delta_load_kwh,
            delta_grid_import_kwh, delta_grid_export_kwh,
            delta_battery_charge_kwh, delta_battery_discharge_kwh, lifetime)
           VALUES (?, ?, 1, 2, 0, 3, 4, 0, 10, 20, 30, 40, 50, 60, 0, 0, 0, 0, 0, 0, 1)""",
        (inverter_id, captured_at.isoformat()),
    )


@pytest.fixture
def legacy_html_contract():
    return json.loads((Path(__file__).parent / "fixtures" / "legacy_html_contract.json").read_text())


@pytest.fixture
def static_mount_contract():
    return json.loads((Path(__file__).parent / "fixtures" / "static_mount_contract.json").read_text())


def test_legacy_html_routes_match_the_golden_form_contract(service, legacy_html_contract):
    """Each Jinja route preserves the form actions and field names it exposes."""

    with TestClient(app) as client:
        for path, expected in legacy_html_contract.items():
            if not path.startswith("/") or not isinstance(expected, dict):
                continue
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            for text in expected["contains"]:
                assert text in response.text
            for form in expected["forms"]:
                action = re.escape(form["action"])
                assert re.search(rf'<form[^>]+action="{action}"', response.text)
                form_html = re.search(rf'<form[^>]+action="{action}".*?</form>', response.text, re.DOTALL).group(0)
                for field in form["fields"]:
                    assert re.search(rf'name="{re.escape(field)}"', form_html)


def test_registered_static_mount_preserves_legacy_asset_contract(static_mount_contract):
    """The registered static mount continues serving the legacy browser assets."""

    with TestClient(app) as client:
        for asset in static_mount_contract:
            response = client.get(asset["path"])
            assert response.status_code == 200, asset["path"]
            assert response.headers["content-type"].startswith(asset["content_type"]), asset["path"]
            for marker in asset["markers"]:
                assert marker in response.text, f"{asset['path']} missing marker: {marker}"


def test_legacy_form_submissions_preserve_303_targets_and_saved_fields(service, monkeypatch):
    """Golden compatibility for every form POST rendered by the legacy pages."""

    plan = service.list_power_plans()[0]
    periods = _tou_for_editor(service.list_tou_periods(plan["id"]))
    monkeypatch.setattr("solarmax.service.fetch_open_meteo", lambda *_: WeatherSummary("test", "test"))
    cases = [
        ("/api/settings", {"theme": "classic-dark", "mode": "manual", "site_name": "Legacy site", "site_lat": "-27.4698", "site_lon": "153.0251", "site_timezone": "Australia/Brisbane", "poll_interval_seconds": "31", "active_plan_id": str(plan["id"])}, "/settings", lambda: service.load_app_settings().site_name == "Legacy site"),
        ("/api/inverters", {"inverter_id": "1", "name": "Legacy inverter", "model": "SigenStor", "adapter_kind": "sigenstor_ec_20_0_tp_au", "battery_reserve_percent": "29"}, "/inverters", lambda: service.get_inverter(1)["name"] == "Legacy inverter"),
        ("/api/plans", {"plan_id": str(plan["id"]), "provider_name": "Legacy provider", "plan_name": "Legacy plan", "billing_cycle": "monthly", "billing_start_day": "1", "billing_start_month": "1"}, "/plans", lambda: service.get_power_plan(plan["id"])["plan_name"] == "Legacy plan"),
        (f"/api/tou/{plan['id']}", {"payload": json.dumps(periods), "daily_supply_charge": "$1.23"}, "/plans", lambda: service.get_power_plan(plan["id"])["daily_supply_charge_cents"] == 123.0),
        ("/api/poll-now", {}, "/", lambda: True),
        ("/api/ai/apply", {"inverter_id": "1"}, "/inverters", lambda: service.get_inverter(1)["battery_reserve_percent"] == 29),
    ]
    with TestClient(app) as client:
        for path, data, location, saved in cases:
            response = client.post(path, data=data, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == location
            assert saved()


def test_all_api_routes_match_the_golden_compatibility_contract(service, legacy_html_contract, monkeypatch):
    """Every current API route has a stable, runtime-independent golden check."""

    monkeypatch.setattr("solarmax.service.fetch_open_meteo", lambda *_: WeatherSummary("test", "test"))
    monkeypatch.setattr(service, "poll_once", lambda: None)
    with TestClient(app) as client:
        for case in legacy_html_contract["api"]:
            response = client.request(case["method"], case["path"], data=case.get("data"), follow_redirects=False)
            assert response.status_code == case["status"], case["path"]
            if "location" in case:
                assert response.headers["location"] == case["location"]
            if "json_keys" in case:
                assert set(response.json()) == set(case["json_keys"]), case["path"]


def test_selected_recommendation_uses_selected_profile_and_unknown_ids_are_404(service, monkeypatch):
    second_id = service.upsert_inverter({"name": "Second", "model": "SigenStor", "adapter_kind": "sigenstor_ec_20_0_tp_au", "battery_reserve_percent": 61, "battery_feed_in_limit_kw": 4.5})
    monkeypatch.setattr("solarmax.service.fetch_open_meteo", lambda *_: WeatherSummary("test", "test"))

    recommendation = service.weather_and_recommendation(second_id)["recommendation"]
    assert recommendation["recommended_reserve_percent"] == 61
    assert recommendation["recommended_feed_in_limit_kw"] == 4.5

    with TestClient(app) as client:
        response = client.post("/api/ai/recommend", data={"inverter_id": second_id})
        negotiated = client.post(
            "/api/ai/recommend",
            data={"inverter_id": second_id},
            headers={"Accept": "application/json"},
        )
        unknown = client.post("/api/ai/recommend", data={"inverter_id": 99999})
        apply_unknown = client.post("/api/ai/apply", data={"inverter_id": 99999}, follow_redirects=False)

    assert response.status_code == 200
    assert set(response.json()) == {"weather", "recommendation"}
    assert response.json()["recommendation"]["recommended_reserve_percent"] == 61
    assert negotiated.status_code == 200
    assert negotiated.json() == {
        "ok": True,
        "redirect_to": "/inverters",
        "resource_id": second_id,
        "data": response.json(),
    }
    assert unknown.status_code == 404
    assert apply_unknown.status_code == 404


def test_unknown_selected_inverter_wins_over_weather_timeout(service, monkeypatch):
    """Selection errors must be deterministic and must not invoke weather."""

    def weather_must_not_run(*_args):
        raise AssertionError("weather must not be fetched for an unknown inverter")

    monkeypatch.setattr("solarmax.service.fetch_open_meteo", weather_must_not_run)
    with TestClient(app) as client:
        recommend = client.post("/api/ai/recommend", data={"inverter_id": 99999}, headers={"Accept": "application/json"})
        apply = client.post("/api/ai/apply", data={"inverter_id": 99999}, headers={"Accept": "application/json"})

    for response in (recommend, apply):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_tou_read_and_save_are_contractual_and_atomic(service, monkeypatch):
    plan = service.list_power_plans()[0]
    before_periods = service.list_tou_periods(plan["id"])
    before_plan = next(p for p in service.list_power_plans() if p["id"] == plan["id"])

    with TestClient(app) as client:
        read = client.get(f"/api/plans/{plan['id']}/tou")
    assert read.status_code == 200
    assert read.json()["plan_id"] == plan["id"]
    assert read.json()["periods"] == before_periods

    invalid_periods = [*before_periods, {"direction": "import", "label": "Broken", "start_minute": 0, "end_minute": 15, "rate_cents_per_kwh": 1.0}]
    with pytest.raises(ValueError):
        service.save_tou_schedule(plan["id"], invalid_periods, 178.0, 8.0, 8.0, 3.0)
    assert service.list_tou_periods(plan["id"]) == before_periods
    # An error after the replacement DELETE has started must still roll back
    # both schedule and tariff data as one database transaction.
    with db_session(service.db_path) as conn:
        conn.execute("""CREATE TRIGGER fail_phase0_tou_insert BEFORE INSERT ON tou_periods
                        WHEN NEW.label = 'Off-peak' BEGIN SELECT RAISE(ABORT, 'simulated insert failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated insert failure"):
        service.save_tou_schedule(plan["id"], before_periods, 178.0, 8.0, 8.0, 3.0)
    assert service.list_tou_periods(plan["id"]) == before_periods
    after_plan = next(p for p in service.list_power_plans() if p["id"] == plan["id"])
    assert after_plan["daily_supply_charge_cents"] == before_plan["daily_supply_charge_cents"]


def test_no_plan_bill_is_normalized_and_billing_window_is_explicit(service):
    service.save_app_settings({"active_plan_id": None})
    with TestClient(app) as client:
        response = client.get("/api/bill")

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] is None
    assert body["rows"] == body["daily"] == []
    assert body["today_grid_import_kwh"] == body["today_grid_export_kwh"] == 0.0
    assert body["supply_charge_cents"] == 0.0
    assert body["supply_charge_days"] == 0
    assert body["billing_window_applied"] is False


def test_live_observed_at_is_the_oldest_required_sample_or_null(service):
    second_id = service.upsert_inverter({"name": "Second", "model": "SigenStor", "adapter_kind": "sigenstor_ec_20_0_tp_au"})
    older = datetime.now(timezone.utc) - timedelta(minutes=4)
    newer = datetime.now(timezone.utc) - timedelta(minutes=1)
    with db_session(service.db_path) as conn:
        conn.execute("UPDATE inverter_profiles SET reachable=1, enabled=1")
        _telemetry(conn, 1, older)
        _telemetry(conn, second_id, newer)

    state = service.dashboard_state()
    assert state["all_reachable"] is True
    assert state["live_observed_at"] == older.isoformat()

    with db_session(service.db_path) as conn:
        conn.execute("UPDATE inverter_profiles SET reachable=0 WHERE id=?", (second_id,))
    assert service.dashboard_state()["live_observed_at"] is None


def test_json_mutation_envelopes_keep_form_redirects_and_weather_errors_stable(service, monkeypatch):
    form_data = {"theme": "classic-dark", "mode": "manual", "site_name": "Solarmax", "site_lat": "-27.4698", "site_lon": "153.0251", "site_timezone": "Australia/Brisbane", "poll_interval_seconds": "30", "active_plan_id": "1"}
    with TestClient(app) as client:
        legacy = client.post("/api/settings", data=form_data, follow_redirects=False)
        json_success = client.post("/api/settings", data=form_data, headers={"Accept": "application/json"})
    assert legacy.status_code == 303 and legacy.headers["location"] == "/settings"
    assert json_success.json()["ok"] is True
    assert json_success.json()["resource_id"] is None
    assert json_success.json()["redirect_to"] == "/settings"

    monkeypatch.setattr("solarmax.service.fetch_open_meteo", lambda *_: (_ for _ in ()).throw(httpx.TimeoutException("timeout")))
    with TestClient(app) as client:
        weather = client.post("/api/ai/recommend", data={"inverter_id": 1}, headers={"Accept": "application/json"})
    assert weather.status_code == 504
    assert weather.json() == {"ok": False, "error": {"code": "weather_timeout", "message": "Weather service timed out"}}


def test_json_mutation_validation_errors_use_the_advertised_envelope(service):
    with TestClient(app) as client:
        missing_required_field = client.post("/api/settings", data={}, headers={"Accept": "application/json"})
        legacy_missing_required_field = client.post("/api/settings", data={})
        invalid_domain_value = client.post(
            "/api/inverters",
            data={"name": "Bad", "model": "SigenStor", "adapter_kind": "sigenstor_ec_20_0_tp_au", "battery_reserve_percent": "101"},
            headers={"Accept": "application/json"},
        )
        malformed_tou = client.post(
            "/api/tou/1",
            data={"payload": "not-json"},
            headers={"Accept": "application/json"},
        )

    for response in (missing_required_field, invalid_domain_value, malformed_tou):
        assert response.status_code == 422
        assert response.json()["ok"] is False
        assert response.json()["error"]["code"] == "validation_error"
        assert isinstance(response.json()["error"]["message"], str)
    assert legacy_missing_required_field.status_code == 422
    assert "detail" in legacy_missing_required_field.json()


def test_json_mutation_successes_have_redirect_targets(service, monkeypatch):
    plan = service.list_power_plans()[0]
    periods = _tou_for_editor(service.list_tou_periods(plan["id"]))
    monkeypatch.setattr("solarmax.service.fetch_open_meteo", lambda *_: WeatherSummary("test", "test"))
    cases = [
        ("/api/settings", {"theme": "classic-dark", "mode": "manual", "site_name": "Solarmax", "site_lat": "-27.4698", "site_lon": "153.0251", "site_timezone": "Australia/Brisbane", "poll_interval_seconds": "30", "active_plan_id": str(plan["id"])}, "/settings"),
        ("/api/inverters", {"name": "New", "model": "SigenStor", "adapter_kind": "sigenstor_ec_20_0_tp_au"}, "/inverters"),
        ("/api/plans", {"provider_name": "Provider", "plan_name": "Plan", "billing_cycle": "monthly", "billing_start_day": "1", "billing_start_month": "1"}, "/plans"),
        (f"/api/tou/{plan['id']}", {"payload": json.dumps(periods)}, "/plans"),
        ("/api/poll-now", {}, "/"),
        ("/api/ai/recommend", {"inverter_id": "1"}, "/inverters"),
        ("/api/ai/apply", {"inverter_id": "1"}, "/inverters"),
    ]
    with TestClient(app) as client:
        responses = [(redirect_to, client.post(path, data=data, headers={"Accept": "application/json"})) for path, data, redirect_to in cases]

    for redirect_to, response in responses:
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["redirect_to"] == redirect_to


def test_legacy_pages_and_typed_read_routes_remain_available(service):
    with TestClient(app) as client:
        pages = [client.get(path) for path in ("/", "/inverters", "/plans", "/billing", "/settings")]
        state = client.get("/api/state")
        chart = client.get("/api/chart?days=1")
        invalid_chart = client.get("/api/chart?days=0")

    assert all(response.status_code == 200 and response.headers["content-type"].startswith("text/html") for response in pages)
    assert state.status_code == 200 and "live_observed_at" in state.json()
    assert chart.status_code == 200 and set(chart.json()) == {"points"}
    assert invalid_chart.status_code == 422


def test_api_responses_are_explicitly_not_cacheable(service):
    with TestClient(app) as client:
        state = client.get("/api/state")
        bill = client.get("/api/bill")
        mutation = client.post("/api/poll-now", headers={"Accept": "application/json"})

    assert state.headers["cache-control"] == "no-store"
    assert bill.headers["cache-control"] == "no-store"
    assert mutation.headers["cache-control"] == "no-store"


def test_openapi_declares_read_and_json_mutation_contracts():
    schema = app.openapi()
    state_schema = schema["paths"]["/api/state"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    tou_schema = schema["paths"]["/api/plans/{plan_id}/tou"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    mutation_responses = schema["paths"]["/api/settings"]["post"]["responses"]

    assert state_schema["$ref"].endswith("/DashboardStateResponse")
    assert tou_schema["$ref"].endswith("/TouPeriodsResponse")
    assert mutation_responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/MutationSuccessResponse")
    assert mutation_responses["404"]["content"]["application/json"]["schema"]["$ref"].endswith("/MutationErrorResponse")
    recommendation_schema = schema["paths"]["/api/ai/recommend"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert recommendation_schema == {
        "anyOf": [
            {"$ref": "#/components/schemas/WeatherRecommendationResponse"},
            {"$ref": "#/components/schemas/MutationSuccessResponse"},
        ],
        "title": "Response Api Ai Recommend Api Ai Recommend Post",
    }
    bill_properties = schema["components"]["schemas"]["BillSummaryResponse"]["properties"]
    assert bill_properties["lines"]["deprecated"] is True
    assert bill_properties["rollups"]["deprecated"] is True


def test_inverter_adapter_kind_writes_reject_unknown_values_and_preserve_sigenstor(service):
    known = service.get_inverter(1)
    assert known["adapter_kind"] == "sigenstor_ec_20_0_tp_au"

    for payload in (
        {"name": "Unsupported", "model": "Other", "adapter_kind": "unknown_adapter"},
        {"id": 1, "name": "Changed", "model": "Other", "adapter_kind": "unknown_adapter"},
    ):
        with pytest.raises(ValidationError) as error:
            service.upsert_inverter(payload)
        assert error.value.errors()[0]["loc"] == ("adapter_kind",)
        assert error.value.errors()[0]["type"] == "literal_error"

    with TestClient(app) as client:
        rejected_new = client.post(
            "/api/inverters",
            data={"name": "Unsupported", "model": "Other", "adapter_kind": "unknown_adapter"},
            headers={"Accept": "application/json"},
        )
        rejected_update = client.post(
            "/api/inverters",
            data={"inverter_id": "1", "name": "Changed", "model": "Other", "adapter_kind": "unknown_adapter"},
            headers={"Accept": "application/json"},
        )

    for response in (rejected_new, rejected_update):
        assert response.status_code == 422
        assert response.json()["ok"] is False
        assert response.json()["error"]["code"] == "validation_error"
        assert "adapter_kind" in response.json()["error"]["message"]
    assert service.get_inverter(1)["adapter_kind"] == "sigenstor_ec_20_0_tp_au"
    assert service.get_inverter(1)["name"] == known["name"]


def test_mcp_contracts_continue_to_share_the_service_layer(service, monkeypatch):
    """Exercise MCP tool functions without requiring a stdio server process."""
    monkeypatch.setattr("solarmax.service.fetch_open_meteo", lambda *_: WeatherSummary("test", "test"))
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")

    class FakeFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self):
            return lambda function: function

    fake_fastmcp.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)
    sys.modules.pop("solarmax.mcp_server", None)
    mcp_server = importlib.import_module("solarmax.mcp_server")
    mcp_server.service = service

    state = mcp_server.get_dashboard_state()
    inverters = mcp_server.list_inverters()
    plans = mcp_server.list_power_plans()
    assert state["inverters"] == inverters
    assert plans == service.list_power_plans()

    updated_profile = mcp_server.update_inverter({
        "id": 1,
        "name": "MCP inverter",
        "model": "SigenStor",
        "adapter_kind": "sigenstor_ec_20_0_tp_au",
        "battery_reserve_percent": 22,
    })
    assert updated_profile["name"] == "MCP inverter"

    updated_settings = mcp_server.set_app_settings({"site_name": "MCP site"})
    assert updated_settings["site_name"] == "MCP site"

    weather_and_recommendation = mcp_server.get_weather_and_recommendation()
    assert set(weather_and_recommendation) == {"weather", "recommendation"}
    assert weather_and_recommendation["weather"]["source"] == "test"

    updated = mcp_server.apply_recommendation(1, {"recommended_reserve_percent": 25, "recommended_feed_in_limit_kw": 1.0})
    assert updated["battery_reserve_percent"] == 25
