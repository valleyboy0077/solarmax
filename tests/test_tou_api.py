import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from solarmax.db import db_session
from solarmax.main import _tou_for_editor, app
from solarmax.service import SolarmaxService


def test_tou_save_accepts_browser_normalized_control_after_edited_time(tmp_path, monkeypatch):
    """A textarea line ending inside an edited time must not fail JSON parsing.

    Browsers normalize textarea line endings in form submissions.  If that
    control character remains with a pasted/edited time, the default strict
    JSON parser rejects the form before the TOU time parser can trim it.
    """

    service = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", service)
    plan = service.list_power_plans()[0]
    periods = service.list_tou_periods(plan["id"])
    payload = json.dumps(_tou_for_editor(periods), indent=2).replace(
        '"start_minute": "09:00"', '"start_minute": "09:00\n"'
    )

    with pytest.raises(json.JSONDecodeError, match="Invalid control character"):
        json.loads(payload)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tou/{plan['id']}",
            data={
                "payload": payload,
                "daily_supply_charge": "$1.78",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    saved = service.list_tou_periods(plan["id"])
    off_peak = next(period for period in saved if period["label"] == "Off-peak")
    assert (off_peak["start_minute"], off_peak["end_minute"]) == (540, 960)
    saved_plan = service.list_power_plans()[0]
    assert saved_plan["daily_supply_charge_cents"] == 178.0
    tomorrow = (datetime.now(ZoneInfo("Australia/Brisbane")).date() + timedelta(days=1)).isoformat()
    with db_session(service.db_path) as conn:
        revision = conn.execute(
            "SELECT effective_from, plan_id FROM billing_plan_revisions WHERE effective_from=?",
            (tomorrow,),
        ).fetchone()
    assert tuple(revision) == (tomorrow, plan["id"])


@pytest.mark.parametrize("legacy_tiers", [
    {"export_tier_kwh": "8", "export_tier_rate_cents_per_kwh": "0", "export_excess_rate_cents_per_kwh": "3"},
    {"export_tier_kwh": "8", "export_tier_rate_cents_per_kwh": "8", "export_excess_rate_cents_per_kwh": "0"},
    {"export_tier_kwh": "8"},
])
def test_tou_api_rejects_deprecated_plan_wide_tiers_without_replacing_schedule(tmp_path, monkeypatch, legacy_tiers):
    service = SolarmaxService(tmp_path / "solarmax.db")
    monkeypatch.setattr("solarmax.main.service", service)
    plan = service.list_power_plans()[0]
    before = service.list_tou_periods(plan["id"])

    with TestClient(app) as client:
        response = client.post(
            f"/api/tou/{plan['id']}",
            data={"payload": "[]", **legacy_tiers},
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "deprecated" in response.json()["error"]["message"]
    assert service.list_tou_periods(plan["id"]) == before
