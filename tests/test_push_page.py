"""2026-09-18: external_ref removed entirely -- vch_no is now REQUIRED on
every payload (see voucher_model.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# DB isolation (idempotency.DB_PATH redirected to a fresh temp file per
# test) is handled automatically by tests/conftest.py's autouse fixture.


def test_push_page_renders_with_sample_loaded():
    r = client.get("/push")
    assert r.status_code == 200
    assert "Push a Voucher" in r.text
    assert "vch_no" in r.text  # sample JSON present in the textarea


def test_push_page_rejects_invalid_json():
    # TestClient.post() follows the POST-Redirect-GET redirect by default
    # (confirmed live -- returns the final 200 page, not the 303 itself).
    r = client.post("/push", data={"voucher_json": "{not valid json"})
    assert r.status_code == 200
    assert "Invalid JSON" in r.text


def test_push_page_rejects_failing_validation():
    r = client.post("/push", data={"voucher_json": '{"series": "ZManual"}'})
    assert r.status_code == 200
    assert "Validation failed" in r.text


def test_no_db_validation_of_any_kind():
    # 2026-09-18: masters-existence preflight was removed entirely, same
    # day/reasoning as the duplicate-number check before it -- "let Busy
    # handle it", now fully consistent regardless of db_backend (the
    # separate access-vs-sqlserver test this replaced is no longer
    # meaningful -- nothing branches on db_backend anymore at all). A
    # completely made-up party/item/sale-type is NOT rejected; the file is
    # generated regardless, and Busy's own Import dialog is the only thing
    # that would ever catch this.
    payload = """{
        "series": "ZManual",
        "vch_date": "2026-09-18",
        "party_name": "Totally Made Up Party That Does Not Exist",
        "sale_type": "Totally Made Up Sale Type",
        "mc_name": "Nowhere",
        "vch_no": "ZManual/26-27/70002",
        "items": [{"item_name": "NOT-A-REAL-ITEM-CODE", "quantity": 1, "list_price": 1, "amount": 1}]
    }"""
    r = client.post("/push", data={"voucher_json": payload})
    assert r.status_code == 200
    assert 'class="result-ok"' in r.text
    assert "SKIPPED" in r.text


def test_refreshing_after_push_does_not_resubmit():
    """Real bug found live: POST /push used to return the result HTML
    directly, so browser refresh literally re-sent the same POST -- with
    Auto-import on, that meant a second real Busy import on every refresh.
    Now POST redirects (303) to a GET url; confirm POST itself is a
    redirect (never processed twice by a refresh) and the GET can be
    fetched independently without re-triggering anything."""
    payload = """{
        "series": "ZManual",
        "vch_date": "2026-09-18",
        "party_name": "Cash",
        "sale_type": "Local-Exempt",
        "mc_name": "Main Store",
        "vch_no": "ZManual/26-27/70003",
        "items": [{"item_name": "01210K0PD00", "quantity": 1, "list_price": 1, "amount": 1}]
    }"""
    r = client.post("/push", data={"voucher_json": payload}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/push?result=")

    # Following the redirect (simulating the browser) shows the result once.
    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    assert 'class="result-ok"' in r2.text

    # Re-fetching the SAME redirect URL again (simulating a page refresh)
    # must NOT re-run the push -- the token is consumed, so it just falls
    # back to a blank form, never a second execution.
    r3 = client.get(r.headers["location"])
    assert r3.status_code == 200
    assert 'class="result-ok"' not in r3.text
