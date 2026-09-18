"""Internal WMS voucher model -- deliberately trimmed to the fields Busy's
'Import Vouchers From Excel' (Sales Import format) is CURRENTLY configured to
accept on this install (see findings/EXCEL_IMPORT_DIALOG.md's Configure screen
dump: VCH_SERIES/VCH_BILL_DATE/VCH_BILL_NO/SALE_PURC_TYPE/PARTY_NAME/MC_NAME
header fields, ITEM_NAME/QUANTITY/LIST_PRICE/AMOUNT/DISCOUNT_PERCENT item
fields). GST%/HSN/party-GSTIN/transport/e-way-bill fields all exist in Busy's
field catalog (confirmed via the Configure field picker) but are NOT yet wired
into this Excel format on the live install -- adding them is a live Busy-side
Configure change (another human click) before this model can carry them.
Do not add fields here that Busy isn't actually configured to read yet --
they'd silently be dropped on import, not an error.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator

# Series this utility is allowed to write into -- never one of Busy's own
# manually-entered series (Main/Wholesale/Showroom/etc., see
# findings/SCHEMA_DISCOVERY.md's Master1 MasterType=21 dump).
# 'ZManual' was created live in Busy by the user specifically for testing this
# integration (Numbering Type=Manual, Duplicate/Blank Voucher Number=Don't
# Allow -- so Busy itself also rejects a dup/blank number on import, on top
# of this utility's own idempotency store). Swap/extend this set once a real
# production series (e.g. 'WMS') is created the same way.
ALLOWED_SERIES = {"ZMANUAL"}


class VoucherItem(BaseModel):
    item_name: str = Field(..., min_length=1)
    quantity: float = Field(..., gt=0)
    list_price: float = Field(..., ge=0)
    amount: float = Field(
        ...,
        ge=0,
        description=(
            "Confirmed live (2026-09-18, VchCode=7687): when sale_type is "
            "'Local-TaxIncl.', this is read as TAX-INCLUSIVE -- Busy reverse-"
            "calculates the ex-tax base + CGST/SGST split from it. Not "
            "verified for any other sale_type."
        ),
    )
    discount_percent: float = Field(0, ge=0, le=100)


class WmsVoucher(BaseModel):
    """One Sales voucher pushed from the WMS toward Busy.

    No external_ref / auto-allocation (removed 2026-09-18, per explicit
    request) -- vch_no is the ONLY identifier a voucher has here, always
    caller-supplied, always used exactly as given. app/idempotency.py's
    get_or_allocate/lookup are no longer called by this model or the push
    endpoints; the module is kept (not deleted) but is currently unused
    dead code. There is no retry-safety/dedup at this layer anymore --
    Busy's own numbering-mode enforcement (Duplicate Voucher Number: Don't
    Allow on the series) is the only thing standing between a genuine
    accidental re-push and a real duplicate voucher.
    """

    series: str = Field(..., min_length=1, description="Must be the WMS-only series, e.g. 'WMS'")
    vch_date: date
    party_name: str = Field(..., min_length=1)
    sale_type: str = Field(
        ...,
        min_length=1,
        description=(
            "Must match a real Busy Sale-Type master name. CONFIRMED LIVE: "
            "'Local-18%' does NOT compute any GST on this import path -- "
            "produces a voucher with GSTInfo=False and no CGST/SGST legs, "
            "no error raised. 'Local-TaxIncl.' DOES compute real CGST+SGST "
            "correctly (verified: VchCode=7687, 18% split, tax-inclusive "
            "AMOUNT reverse-calculated to ex-tax base). Use 'Local-TaxIncl.' "
            "for any real taxed voucher; 'Local-Exempt' for genuinely "
            "untaxed (both confirmed working). Any other Sale-Type name is "
            "unverified -- don't assume it behaves like either of these."
        ),
    )
    mc_name: str = Field(..., min_length=1, description="Material Centre / Godown name")
    items: list[VoucherItem] = Field(..., min_length=1)
    vch_no: str = Field(
        ...,
        min_length=1,
        description=(
            "REQUIRED manual voucher number, e.g. 'ZManual/26-27/00099'. Used exactly as given "
            "-- no auto-allocation, no local or remote duplicate check. The caller is fully "
            "responsible for picking a number Busy doesn't already have."
        ),
    )

    @field_validator("series")
    @classmethod
    def series_must_be_allowed(cls, v: str) -> str:
        if v.upper() not in ALLOWED_SERIES:
            raise ValueError(
                f"series {v!r} is not in ALLOWED_SERIES ({sorted(ALLOWED_SERIES)}). "
                "This utility refuses to write into any of Busy's own manually-entered series."
            )
        return v
