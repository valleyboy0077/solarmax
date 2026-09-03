from solarmax.db import db_session, set_setting
from solarmax.service import SolarmaxService


def test_retired_stored_theme_falls_back_to_classic_dark(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    with db_session(service.db_path) as conn:
        set_setting(conn, "theme", "solar-glass")

    assert service.load_app_settings().theme == "classic-dark"


def test_supported_stored_theme_is_loaded(tmp_path):
    service = SolarmaxService(tmp_path / "solarmax.db")
    with db_session(service.db_path) as conn:
        set_setting(conn, "theme", "ember-core")

    assert service.load_app_settings().theme == "ember-core"
