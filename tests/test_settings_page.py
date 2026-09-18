import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app import settings_store
from app.main import app

client = TestClient(app)

# DB isolation (settings_store.DB_PATH redirected to a fresh temp file per
# test) is handled automatically by tests/conftest.py's autouse fixture.


def test_settings_page_renders_with_defaults():
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Voucher Type" in r.text
    assert "Sales Import" in r.text  # default value shown


def test_settings_page_persists_changes():
    r = client.post(
        "/settings",
        data={"voucher_type": "Sales", "format_name": "Sales Import V2"},
    )
    assert r.status_code in (200, 303)
    assert settings_store.get("format_name") == "Sales Import V2"


def test_unchecked_checkboxes_persist_as_off():
    settings_store.set_value("automation_armed", "1")
    client.post("/settings", data={"voucher_type": "Sales", "format_name": "Sales Import"})
    assert settings_store.get_bool("automation_armed") is False
