"""Phase 1 rollout and static-cache contracts."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import solarmax.main as main
from solarmax.config import RuntimeConfig
from solarmax.service import SolarmaxService


def test_react_rollout_is_opt_in_and_only_handles_existing_page_routes(tmp_path, monkeypatch):
    service = SolarmaxService(tmp_path / "solarmax.db")
    index = tmp_path / "webui" / "index.html"
    index.parent.mkdir()
    index.write_text("<!doctype html><title>React shell</title>")
    monkeypatch.setattr(main, "service", service)
    monkeypatch.setattr(main, "WEBUI_INDEX", index)

    monkeypatch.setattr(main, "config", RuntimeConfig(webui_mode="legacy"))
    with TestClient(main.app) as client:
        legacy = client.get("/")
        unknown = client.get("/not-a-page")
    assert "React shell" not in legacy.text
    assert unknown.status_code == 404

    monkeypatch.setattr(main, "config", RuntimeConfig(webui_mode="react"))
    with TestClient(main.app) as client:
        for path in ("/", "/inverters", "/plans", "/billing", "/settings"):
            response = client.get(path)
            assert response.status_code == 200
            assert "React shell" in response.text
            assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
        assert client.get("/api/state").headers["content-type"].startswith("application/json")
        assert client.get("/not-a-page").status_code == 404


def test_fingerprinted_react_assets_are_immutable(tmp_path, monkeypatch):
    asset = Path(main.__file__).parent / "static" / "webui" / "assets" / "app-test.js"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text("console.log('test')")
    index = Path(main.__file__).parent / "static" / "webui" / "index.html"
    original_index = index.read_bytes() if index.exists() else None
    index.write_text("<!doctype html><title>test index</title>")
    try:
        with TestClient(main.app) as client:
            response = client.get("/static/webui/assets/app-test.js")
            index_response = client.get("/static/webui/index.html")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert index_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    finally:
        asset.unlink(missing_ok=True)
        if original_index is None:
            index.unlink(missing_ok=True)
        else:
            index.write_bytes(original_index)
