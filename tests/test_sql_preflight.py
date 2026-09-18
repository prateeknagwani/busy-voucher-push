"""Live, read-only tests against the real Busy database via busy_sync_reader.

These are integration tests, not unit tests -- they require the live SQL
Server to be reachable and config.local.json to hold real read-only
credentials. Skipped automatically if that connection can't be made.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.sql_preflight import connect_readonly, preflight_check
from app.voucher_model import VoucherItem, WmsVoucher

try:
    _conn = connect_readonly()
    _conn.close()
    LIVE_DB_AVAILABLE = True
except Exception:
    LIVE_DB_AVAILABLE = False

pytestmark = pytest.mark.skipif(not LIVE_DB_AVAILABLE, reason="live Busy SQL Server not reachable")


def _real_voucher(vch_no="ZManual/26-27/99999") -> WmsVoucher:
    """Uses the exact real masters our own successful manual test voucher
    (ZManual/26-27/00001, VchCode 7671) used -- known-good, confirmed live."""
    return WmsVoucher(
        series="ZManual",
        vch_date=date(2026, 9, 18),
        party_name="Cash",
        sale_type="Local-Exempt",
        mc_name="Main Store",
        vch_no=vch_no,
        items=[VoucherItem(item_name="01210K0PD00", quantity=2, list_price=100, amount=200, discount_percent=0)],
    )


def test_all_real_masters_resolve_cleanly():
    result = preflight_check(_real_voucher(), candidate_vch_no="ZManual/26-27/99999")
    assert result.ok, result.errors
    assert result.resolved.series_full_name == "09ZManual"
    assert result.resolved.item_codes["01210K0PD00"] > 0


def test_duplicate_voucher_number_is_no_longer_checked_here():
    # 2026-09-18: the Tran1 duplicate check was removed from preflight_check
    # per explicit request ("let Busy handle it") -- ZManual/26-27/00001
    # genuinely already exists (our real first test import) but preflight
    # now passes it through anyway; Busy's own numbering-mode enforcement
    # is the sole remaining duplicate check, at Import time.
    result = preflight_check(_real_voucher(), candidate_vch_no="ZManual/26-27/00001")
    assert result.ok, result.errors


def test_unknown_party_is_rejected():
    v = _real_voucher()
    v = v.model_copy(update={"party_name": "This Party Definitely Does Not Exist Ltd."})
    result = preflight_check(v, candidate_vch_no="ZManual/26-27/99998")
    assert not result.ok
    assert any("Party not found" in e for e in result.errors)


def test_unknown_item_is_rejected():
    v = _real_voucher()
    v.items[0].item_name = "ZZZ-NONEXISTENT-ITEM-9999"
    result = preflight_check(v, candidate_vch_no="ZManual/26-27/99997")
    assert not result.ok
    assert any("Item not found" in e for e in result.errors)


def test_unknown_series_is_rejected():
    v = _real_voucher()
    # model_copy(update=...) does not re-run validators (pydantic v2), so this
    # can carry a series value that would fail WmsVoucher's own ALLOWED_SERIES
    # check -- deliberately, to isolate testing the preflight's live-DB
    # existence check from that separate, already-covered guard.
    v2 = v.model_copy(update={"series": "NoSuchSeriesXYZ"})
    result2 = preflight_check(v2, candidate_vch_no="X")
    assert not result2.ok
    assert any("series not found" in e for e in result2.errors)
