# Sample voucher payloads

Ready-to-paste JSON for `POST /vouchers` (via Swagger UI at `/docs`, or the
`/push` page's sample buttons).

All 4 use real, confirmed-existing masters (`Cash` party, `Main Store`
material centre, item `01210K0PD00`) against the `ZManual` test series.

| File | What it tests |
|---|---|
| `01_simple_exempt.json` | Basic single-item, untaxed voucher |
| `02_taxed_with_discount.json` | Real 18% GST (CGST+SGST) + a 10% line discount |
| `03_multi_item.json` | Two line items in one voucher, mixed discounts |
| `04_manual_voucher_number.json` | Same shape as (1), different number |

**`vch_no` is REQUIRED on every voucher** (2026-09-18: `external_ref` and
auto-allocation were removed entirely — see `app/voucher_model.py`). There
is no idempotency tracking and no duplicate-number check anywhere in this
tool anymore ("let Busy handle it") — **before reusing any of these
samples, change `vch_no` to a number you know is genuinely unused**, or
Busy's own numbering-mode enforcement (Duplicate Voucher Number: Don't
Allow) is the only thing standing between you and a real duplicate voucher
attempt at Import time.
