"""Safe plan mutation contracts."""
from __future__ import annotations

from fastapi.testclient import TestClient

from solarmax.db import create_billing_plan_revision, db_session
from solarmax.main import app
from solarmax.service import SolarmaxService


def _new_plan(service: SolarmaxService, name: str) -> int:
    return service.upsert_power_plan({
        "provider_name": "Test provider",
        "plan_name": name,
        "billing_cycle": "monthly",
        "billing_start_day": 1,
        "billing_start_month": 1,
    })


def test_delete_plan_returns_json_and_removes_only_an_unused_plan(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", service)
    plan_id = _new_plan(service, "Unused plan")

    with TestClient(app) as client:
        response = client.delete(f"/api/plans/{plan_id}", headers={"Accept": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "redirect_to": "/plans", "resource_id": plan_id, "data": None}
    assert service.get_power_plan(plan_id) is None


def test_delete_plan_rejects_unknown_active_and_last_plan(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", service)
    second_id = _new_plan(service, "Second plan")

    with TestClient(app) as client:
        unknown = client.delete("/api/plans/999", headers={"Accept": "application/json"})
        active = client.delete("/api/plans/1", headers={"Accept": "application/json"})

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"
    assert active.status_code == 409
    assert active.json()["error"]["code"] == "active_plan"
    assert service.get_power_plan(1) is not None

    last_service = SolarmaxService(tmp_path / "last-plan.db")
    monkeypatch.setattr("solarmax.main.service", last_service)
    last_service.save_app_settings({"active_plan_id": None})
    with TestClient(app) as client:
        last = client.delete("/api/plans/1", headers={"Accept": "application/json"})

    assert last.status_code == 409
    assert last.json()["error"]["code"] == "last_plan"
    assert last_service.get_power_plan(1) is not None


def test_delete_plan_rejects_billing_history_and_telemetry_references(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", service)
    history_id = _new_plan(service, "Historical plan")
    telemetry_id = _new_plan(service, "Telemetry plan")

    with db_session(service.db_path) as conn:
        history_revision_id = create_billing_plan_revision(conn, history_id, "2099-01-01")
        telemetry_revision_id = create_billing_plan_revision(conn, telemetry_id, "2099-01-01")
        conn.execute(
            """INSERT INTO telemetry_rollups
               (inverter_id, bucket_start, bucket_end, solar_kwh, load_kwh,
                grid_import_kwh, grid_export_kwh, battery_charge_kwh,
                battery_discharge_kwh, amount_cents, pricing_revision_id)
               VALUES (1, '2099-01-01T00:00:00+00:00', '2099-01-01T00:30:00+00:00',
                       0, 0, 0, 0, 0, 0, 0, ?)""",
            (telemetry_revision_id,),
        )

    with TestClient(app) as client:
        history = client.delete(f"/api/plans/{history_id}", headers={"Accept": "application/json"})
        telemetry = client.delete(f"/api/plans/{telemetry_id}", headers={"Accept": "application/json"})

    assert history.status_code == 409
    assert history.json()["error"]["code"] == "billing_plan_revisions"
    assert telemetry.status_code == 409
    assert telemetry.json()["error"]["code"] == "telemetry_references"
    assert service.get_power_plan(history_id) is not None
    assert service.get_power_plan(telemetry_id) is not None
    with db_session(service.db_path) as conn:
        assert conn.execute("SELECT 1 FROM billing_plan_revisions WHERE id=?", (history_revision_id,)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM telemetry_rollups WHERE pricing_revision_id=?", (telemetry_revision_id,)).fetchone() is not None
