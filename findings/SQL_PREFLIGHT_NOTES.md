# SQL preflight validation — findings

Read-only work, `busy_sync_reader` only, confirmed live against real data
(our own test voucher `ZManual/26-27/00001`, VchCode 7671).

## Column mapping confirmed on `Tran1` (Sales voucher, VchType=9)

| Column | Role | Confirmed value on VchCode 7671 |
|---|---|---|
| `MasterCode1` | Party ledger → `Master1` MasterType=2 | 1 = "Cash" |
| `MasterCode2` | Material Centre → `Master1` MasterType=11 | 201 = "Main Store" |
| `CM1` | Sale Type → `Master1` MasterType=13 | 1215 = "Local-Exempt" |
| `VchSeriesCode` | Voucher Series → `Master1` MasterType=21 | 14717 = "09ZManual" |
| `VchNo` | Display voucher number | `"      ZManual/26-27/00001"` — **right-justified, space-padded to a fixed width (25 chars)**, not stored as the plain string |

## `Tran2` legs on the same voucher — one more RecType discovered

`RecType=3` = Bill Sundry leg (`MasterType=9`, e.g. "Rounded Off (+)"/"Rounded Off (-)") —
a third leg type alongside the already-known `RecType=1` (ledger) / `RecType=2` (item).
Not previously documented in `busy-extraction-poc/FINDINGS.md`.

## Two real gotchas a naive preflight would have missed

1. **Voucher Series names are auto-prefixed by Busy.** We created a series
   named "ZManual" in Busy's UI; it's actually stored as `Master1.Name =
   "09ZManual"` (a 2-digit sort prefix, same pattern as every other series
   seen: "02Main", "09Wholesale", "12SalesOrder"). An exact-string match
   against the name we typed would never find it. Fixed via an ends-with,
   case-insensitive match (`sql_preflight.py::_resolve_series`) — rejected as
   ambiguous if more than one series name ends with the given suffix.
2. **`VchNo` is space-padded in storage.** A real duplicate-number check
   against an unpadded candidate string silently misses genuine duplicates —
   confirmed by writing a test that should have failed and didn't
   (`test_existing_voucher_number_is_flagged_as_duplicate`, initially a false
   negative). Fixed via `LTRIM(RTRIM(...))` on both sides of the comparison.
   **This also matters for the eventual INSERT** — a new row should probably
   be padded to the same fixed width Busy itself uses, for consistent
   sort/display behavior; exact required width not yet confirmed beyond this
   one 25-char sample (`"ZManual/26-27/00001"` is 19 chars + 6 leading
   spaces — unclear if 25 is fixed or scales with something else; needs
   another real example at a different length to confirm).

## What `app/sql_preflight.py` guarantees today

Given a `WmsVoucher` + a candidate display voucher number, `preflight_check()`
resolves party/sale-type/material-centre/every item/series against real,
existing `Master1` rows and checks the candidate number doesn't already
exist in `Tran1` for that series — all via the read-only login, all before
any write is attempted. Returns a precise, itemized error list on any
failure; never a partial/best-guess resolution. 5 live tests cover: full
clean resolution, real-duplicate detection, unknown party, unknown item,
unknown series.

**What it does NOT yet check**: HSN/GST-rate consistency, master-pack
quantity rounding, credit-limit or other business-rule validation Busy's own
app layer might apply beyond plain existence — this is existence-and-
duplicate validation only, not a full re-implementation of Busy's own save
logic.
