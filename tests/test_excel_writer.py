"""Round-trips a sample voucher through the Excel-generation path and checks
the output matches the EXACT column layout/grouping confirmed live against
the real Busy install (see findings/EXCEL_IMPORT_DIALOG.md).

This does not touch Busy or any database -- it is a pure file-shape test,
the equivalent of the original plan's "confirm the resulting invoice matches
what Busy's own UI would produce" ask, scoped to what's checkable without a
live import (column layout / header-only-on-first-row grouping / numbering).

2026-09-18: external_ref and auto-allocation were removed entirely (see
voucher_model.py) -- vch_no is now REQUIRED and always used exactly as
given. Every test below reflects that; there is no idempotency/duplicate-
number behavior left to test in this module.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from openpyxl import load_workbook

from app.excel_writer import COLUMNS, write_vouchers_xlsx
from app.voucher_model import VoucherItem, WmsVoucher


def _sample_voucher(vch_no="ZManual/26-27/00001") -> WmsVoucher:
    return WmsVoucher(
        series="ZManual",
        vch_date=date(2026, 9, 18),
        party_name="Busy Infotech Pvt. Ltd.",
        sale_type="Local-18%",
        mc_name="Main Store",
        vch_no=vch_no,
        items=[
            VoucherItem(item_name="Item 01", quantity=10, list_price=100, amount=1000, discount_percent=0),
            VoucherItem(item_name="Item 02", quantity=20, list_price=200, amount=4000, discount_percent=5),
        ],
    )


def test_column_headers_match_confirmed_busy_layout(tmp_path):
    out = write_vouchers_xlsx([_sample_voucher()], tmp_path / "out.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    header_row = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    assert header_row == COLUMNS


def test_header_fields_only_on_first_item_row(tmp_path):
    out = write_vouchers_xlsx([_sample_voucher()], tmp_path / "out.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 2

    first, second = rows
    assert first[0] == "ZManual"  # VCH_SERIES
    assert first[2] == "ZManual/26-27/00001"  # VCH/BILL_NO
    assert first[4] == "Busy Infotech Pvt. Ltd."  # PARTY_NAME
    assert first[6] == "Item 01"  # ITEM_NAME

    # second item row: header columns A-F must be blank, matching Busy's own
    # generated sample convention (findings/13_sample_saleVch.xlsx)
    assert second[0:6] == (None, None, None, None, None, None)
    assert second[6] == "Item 02"
    assert second[10] == 5  # DISCOUNT_PERCENT


def test_series_rejects_busys_own_manual_series():
    with pytest.raises(ValueError):
        WmsVoucher(
            series="SR",  # Busy's own manual series -- must be rejected
            vch_date=date(2026, 9, 18),
            party_name="Cash",
            sale_type="Local-Exempt",
            mc_name="Main Store",
            vch_no="SR/26-27/00001",
            items=[VoucherItem(item_name="Item 01", quantity=1, list_price=1, amount=1)],
        )


def test_vch_no_is_required():
    with pytest.raises(ValueError):
        WmsVoucher(
            series="ZManual",
            vch_date=date(2026, 9, 18),
            party_name="Cash",
            sale_type="Local-Exempt",
            mc_name="Main Store",
            items=[VoucherItem(item_name="Item 01", quantity=1, list_price=1, amount=1)],
        )


def test_vch_no_used_exactly_as_given(tmp_path):
    v = _sample_voucher(vch_no="ZManual/26-27/09999")
    out = write_vouchers_xlsx([v], tmp_path / "manual.xlsx")
    wb = load_workbook(out)
    row = list(wb.active.iter_rows(min_row=2, max_row=2, values_only=True))[0]
    assert row[2] == "ZManual/26-27/09999"


def test_two_vouchers_with_identical_vch_no_is_allowed_no_check(tmp_path):
    # 2026-09-18: all local clash/duplicate detection was removed per
    # explicit request ("let Busy handle it" / "remove ext ref") -- this
    # module will happily write two vouchers sharing the same number into
    # the same file; nothing here catches it. Busy's own numbering-mode
    # enforcement is the only remaining check, at Import time. This test
    # documents that this is now expected, not a bug.
    v1 = _sample_voucher(vch_no="ZManual/26-27/08887")
    v2 = _sample_voucher(vch_no="ZManual/26-27/08887")
    out = write_vouchers_xlsx([v1, v2], tmp_path / "dup.xlsx")
    wb = load_workbook(out)
    rows = [r for r in wb.active.iter_rows(min_row=2, values_only=True) if r[2]]
    assert [r[2] for r in rows] == ["ZManual/26-27/08887", "ZManual/26-27/08887"]
