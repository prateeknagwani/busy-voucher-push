"""Tests for POST /vouchers. No longer needs a live DB (the LIVE_DB_AVAILABLE
skip condition was removed 2026-09-18 along with preflight validation itself
-- see app/main.py's module docstring) -- these are now fast, fully
DB-independent unit tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.main import OUTBOX_DIR, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_outbox():
    for f in OUTBOX_DIR.glob("TESTENDPOINT-*"):
        f.unlink()
    yield
    for f in OUTBOX_DIR.glob("TESTENDPOINT-*"):
        f.unlink()


def _payload(vch_no, item_name="01210K0PD00"):
    return {
        "series": "ZManual",
        "vch_date": "2026-09-18",
        "party_name": "Cash",
        "sale_type": "Local-Exempt",
        "mc_name": "Main Store",
        "vch_no": vch_no,
        "items": [
            {"item_name": item_name, "quantity": 1, "list_price": 50, "amount": 50, "discount_percent": 0}
        ],
    }


def test_valid_voucher_writes_file_no_db_check():
    resp = client.post("/vouchers", json=_payload("TESTENDPOINT-OK-1"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "FILE_READY_NOT_IMPORTED"
    assert body["preflight"].startswith("SKIPPED")
    assert Path(body["xlsx_path"]).exists()


def test_unknown_item_is_no_longer_rejected_no_preflight():
    # 2026-09-18: masters-existence preflight was removed entirely, same
    # day/reasoning as the duplicate-number check before it -- "let Busy
    # handle it". A nonexistent item name is NOT rejected here anymore;
    # the file is generated regardless, and Busy's own Import dialog is
    # the only thing that would ever catch this.
    resp = client.post("/vouchers", json=_payload("TESTENDPOINT-BAD-1", item_name="ZZZ-NOPE-9999"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "FILE_READY_NOT_IMPORTED"
    assert Path(body["xlsx_path"]).exists()


def test_automation_exception_does_not_500_the_request(monkeypatch):
    """A real bug hit live: an uncaught exception inside run_one_import
    (e.g. a pywinauto TimeoutError because Busy wasn't in the expected
    state) 500'd the whole request even though the .xlsx had already been
    generated successfully. Must degrade to a reported AUTOMATION_ERROR
    status instead."""
    from app import settings_store

    settings_store.set_value("auto_import_on_push", "1")
    try:
        monkeypatch.setattr(
            "app.busy_automation.run_one_import",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated automation failure")),
        )
        resp = client.post("/vouchers", json=_payload("TESTENDPOINT-AUTOMATION-ERR"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "AUTOMATION_ERROR"
        assert "simulated automation failure" in body["import_result"]["error"]
    finally:
        settings_store.set_value("auto_import_on_push", "0")
