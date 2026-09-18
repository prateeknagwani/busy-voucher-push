# Voucher-Push: Schema Discovery (Step 1)

Connected read-only as `busy_sync_reader` (existing login from `busy-extraction-poc/`,
already proven `db_datareader`-only with an explicit DENY on every write/DDL verb) against
the same live production Busy database `busy-extraction-poc/FINDINGS.md` has been reading
from (server/database names and company identity redacted for this handoff — see that
file's own redaction note for the convention used throughout this project). Raw query
output referenced below (this folder's numbered `.txt` files) isn't included in this repo —
every finding that matters from them is already summarized in prose here.

**This is a real, live, in-use accounting database — real invoices are being posted to it
today (most recent Sales voucher at query time: `TI/26-27/4556`, dated 2026-09-18, i.e. today).**

## The 5 tables you asked me to map

| Category | Table | Confirmed |
|---|---|---|
| Voucher header | `Tran1` | `VchCode`(PK), `VchType`, `VchSeriesCode`→`Master1` MasterType=21, `VchNo` (display string e.g. `TI/26-27/4556`), `AutoVchNo` (int, the running number component), `Date`, `MasterCode1`(party ledger), `VchAmtBaseCur`(tax-inclusive total), `VchSalePurcAmt`(ex-tax goods subtotal) — plus a large amount of e-Invoice/GST-return state (see Red Flags) |
| Voucher line items | `Tran2` (RecType=1 ledger legs / RecType=2 item legs), `Tran3` (RecType=4, non-accounting voucher types like Sales Order) | Already fully decoded in `busy-extraction-poc/FINDINGS.md` Folios 0-11 — item qty/rate/discount/MRP, ledger Dr/Cr legs, sign conventions |
| Stock ledger (qty movement) | **No dedicated table** — stock quantity only exists as the sum of `Tran2 RecType=2` item legs over time (perpetual, app-computed), same conclusion `busy-extraction-poc` already reached for read-side WAC | See Red Flags — nothing to "update", which is itself the danger |
| Account ledger / party balance | **No dedicated running-balance table either** — party balance = `Folio1.D1` (genesis opening balance, confirmed Folio 09) + signed sum of every `Tran2 RecType=1` leg since. **`DailySum` is a real cached DAILY aggregate** (`MasterCode1/2`, `MasterType`, `Date`, `Dr1-3`, `Cr1-3`, `D1-4`) — looks like Busy's own performance cache for ledger/report queries, not the source of truth, but a write that doesn't also maintain it will make Busy's own reports disagree with the raw ledger until Busy recomputes it | Purpose/write pattern of `DailySum` unconfirmed — didn't touch it |
| Series / next-voucher-number counter | **Does not exist as a stored counter.** `Master1` MasterType=21 = Voucher Series master (`"09Wholesale"`→Code 258, `"09Showroom"`→Code 14618, etc.) — but every `D`/`I`/`B` slot on these rows is 0 (see `04_voucher_series_master.txt`). The next number is derived live by Busy's app as `MAX(AutoVchNo) WHERE VchSeriesCode=x` + 1 at the moment a new voucher is saved. **See Red Flag #1 — this is the single biggest problem for this whole plan.** |

## Red flags found during discovery — read before writing any insert logic

**1. No database-level uniqueness on voucher numbers — Busy's app is the only thing preventing a collision.**
`sys.indexes` on `Tran1` (`05_tran1_indexes.txt`) shows an index on `(VchSeriesCode, AutoVchNo)`
but `is_unique = 0`. SQL Server itself will not stop two rows sharing the same series+number.
Combined with there being no stored counter to lock (previous point), a WMS-side writer computing
`MAX(AutoVchNo)+1` and inserting has a genuine, unmitigated race window against Busy's own live UI
(this dealer is actively posting invoices through it right now) — two processes can read the same
MAX at once and both insert, producing two vouchers with the **identical GST invoice number**. Under
GST rules a duplicate invoice number is a compliance problem, not just a cosmetic one, and it would
likely also break GSTR filing / e-invoice generation for both vouchers. **A WMS-only series
(`WMS/25-26/xxxxx`) sidesteps colliding with Busy's manual numbering, but does NOT by itself solve
this** — two WMS-originated pushes racing each other (e.g. a retry after a timeout) hit the exact
same unprotected-MAX pattern within the WMS's own series. This needs an explicit locking strategy
(e.g. `sp_getapplock` around read-MAX-then-insert, in the same transaction) that Busy's own app
almost certainly already implements at a layer we can't see or reuse.

**2. This Busy install has live e-Invoice (GST IRN) integration wired in.**
`Tran1` carries `EInvIRN`, `EInvAckNo`, `EInvAckDate`, `EInvSignedQRCode`, `EInvSignedInvoice`
(×10 columns), plus `GSTRecType`/`GSTR2Status`/`GSTR2BStatus`/`ITCClaimedStatus`/`ReturnStatus`.
If e-invoicing is mandatory for this business (turnover-based GST threshold) or even just
enabled, Busy's own app generates the IRN via a government API call at save time and writes the
result back into these columns. **A row inserted directly via SQL will never get an IRN** — it
will look like a normal Sales invoice in the table but be a GST-non-compliant document if
e-invoicing applies to this dealer. This needs a direct answer from you (or your accountant/GST
filer) before proceeding: *is e-invoicing turned on for this GSTIN, and if so, does every voucher
need one, or only above a threshold?* I did not check whether it's actually active (would mean
reading real invoice rows' `EInvIRN` values — holding off until you confirm you want me looking at
that).

**3. `DailySum` is a real cache, not a live view — a write that skips it will make Busy's own reports briefly wrong.**
Confirmed shape (`06_dailysum_columns.txt`): per-ledger, per-day Dr/Cr aggregate. Busy's screens
(ledger reports, trial balance, etc.) likely read this cache rather than re-summing every voucher
live, the same way this app's own `tally_agg_daily`/`tally_agg_partner_monthly` work. A raw
`Tran1`/`Tran2` insert that doesn't also update (or trigger a recompute of) `DailySum` means Busy's
own UI can show a stale balance for that party/date until something rebuilds it — unconfirmed
whether Busy auto-recomputes this on next login/report-open or needs an explicit rebuild action.
Not yet investigated what triggers a `DailySum` refresh.

**4. No database triggers exist on any of these tables.**
`05_tran1_indexes.txt` also checked `sys.triggers` on Tran1/Tran2/Tran3/Master1/Folio1 — none.
This confirms points 1-3 aren't accidentally handled by DB-level automation we might be missing —
literally everything (numbering atomicity, e-invoice calls, DailySum maintenance, GST return
staging) happens in Busy's own application code, invisible to a SQL-level writer. This matches
what you already flagged from experience with the costing-mode issue — it's the general shape of
the risk, not a one-off.

**5. `Folio1` (opening-balance table, per `busy-extraction-poc` Folio 09-11) still has ~146 unconfirmed `D` columns.**
Whatever this table's non-opening-balance columns represent, an insert path touching account/stock
masters at all would need to either leave them untouched (safe default) or understand them first
(not attempted — out of scope for a *voucher* writer, since new vouchers don't create new Folio1
rows, but flagging since your ask mentioned "account ledger / balance table").

## What I did NOT do (deliberately, pending your input)

- Did not create a write-capable SQL login. Creating one is a real security/DDL change against a
  live production accounting database — I'm treating that the same way `busy-extraction-poc`
  treated creating `busy_sync_reader` itself: needs your explicit OK, not assumed from "proceed."
- Did not read any real invoice's `EInvIRN`/e-invoice fields to check whether e-invoicing is
  actually active for this GSTIN — that's real filing-status data, holding off until you say you
  want it looked at.
- Did not write any INSERT/UPDATE logic yet.

## My actual recommendation, given what's above

Point 1 and 2 together mean the direct-SQL path as scoped ("single SQL transaction, header + lines
+ stock + ledger balance + series counter") is riskier than it looked from the Tally2Busy analogy —
Tally2Busy's own documented pattern is bulk historical import scoped by date range, run when Busy is
closed/not being actively used, which sidesteps the live-race problem entirely; this ask is
real-time single-voucher push while Busy's own UI is in concurrent use by staff, which is a
materially different risk profile. I'd rather build and prove the **XML-import fallback path
first** (Busy's own Data Import feature already handles numbering/IRN/DailySum exactly the way a
manually-typed voucher would, since it goes through the same app code) and treat direct-SQL as a
later optimization only once we've watched the XML path work correctly for a while — rather than
the reverse. Your call either way; tell me which to build next.
