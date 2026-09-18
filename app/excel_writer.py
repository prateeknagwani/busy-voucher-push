"""Builds a Busy-Sale-Import-compatible .xlsx, matching the EXACT column
layout confirmed live against this install's Configure screen (see
findings/EXCEL_IMPORT_DIALOG.md's user-supplied screenshot):

    A: VCH_SERIES        (Header Field)
    B: VCH/BILL_DATE      (Header Field)
    C: VCH/BILL_NO        (Header Field)
    D: SALE/PURC_TYPE     (Header Field)
    E: PARTY_NAME         (Header Field)
    F: MC_NAME             (Header Field)
    G: ITEM_NAME           (Item Field)
    H: QUANTITY            (Item Field)
    I: LIST_PRICE          (Item Field)
    J: AMOUNT              (Item Field)
    K: DISCOUNT_PERCENT    (Item Field)

Header-row fields (A-F) are populated ONLY on the first item row of each
voucher and left blank on every subsequent item row of that same voucher --
confirmed as Busy's own grouping convention from its generated sample file
(findings/13_sample_saleVch.xlsx). Getting this grouping wrong (e.g. repeating
the header on every row) has NOT been tested against a real import and should
not be assumed safe.

This module does NOT talk to Busy at all -- it only produces a file on disk.
Nothing here writes into the live database or drives the UI.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from .voucher_model import WmsVoucher

COLUMNS = [
    "VCH_SERIES",
    "VCH/BILL_DATE",
    "VCH/BILL_NO",
    "SALE/PURC_TYPE",
    "PARTY_NAME",
    "MC_NAME",
    "ITEM_NAME",
    "QUANTITY",
    "LIST_PRICE",
    "AMOUNT",
    "DISCOUNT_PERCENT",
]


def _fy_label(vch_date) -> str:
    """Indian FY label, e.g. 2026-04-xx..2027-03-xx -> '26-27'."""
    start_year = vch_date.year if vch_date.month >= 4 else vch_date.year - 1
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def voucher_display_number(v: WmsVoucher) -> str:
    """v.vch_no is required and used exactly as given -- no auto-allocation,
    no idempotency store (removed 2026-09-18, per explicit request)."""
    return v.vch_no


def write_vouchers_xlsx(vouchers: list[WmsVoucher], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(COLUMNS)

    for v in vouchers:
        display_no = voucher_display_number(v)
        for i, item in enumerate(v.items):
            if i == 0:
                header_cells = [
                    v.series,
                    v.vch_date,
                    display_no,
                    v.sale_type,
                    v.party_name,
                    v.mc_name,
                ]
            else:
                header_cells = [None] * 6
            ws.append(
                header_cells
                + [
                    item.item_name,
                    item.quantity,
                    item.list_price,
                    item.amount,
                    item.discount_percent,
                ]
            )

    wb.save(out_path)
    return out_path
