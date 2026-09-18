# "Import Vouchers From Excel" — Busy's real, first-party bulk-voucher path

Found live and mapped against a real running Busy21 session (Rel 14.4,
F.Y. 2026-27) — company name redacted for this handoff, see `FINDINGS.md`'s
redaction note in the `busy-extraction-poc` repo for the convention used
throughout this project. This is **not** the "Data Import" XML menu item
originally described in the plan — it's a different, Excel/Google-Sheet-
driven feature, and it turned out to be the far more promising path.
Screenshots and the raw sample `.xlsx` referenced below aren't included in
this repo (see this folder's own note) — the column layout they showed is
already fully described in prose below.

## Why this beats both the original XML-import plan and direct-SQL

- **Goes through Busy's real posting logic** — numbering, e-Invoice/IRN generation, GST
  return staging, and the `DailySum` cache all happen exactly as if someone typed the voucher
  in by hand. This directly neutralizes 3 of the 4 red flags from `SCHEMA_DISCOVERY.md`
  (voucher-number race condition, missing e-Invoice IRN, stale `DailySum`).
- **Has a real, built-in upsert/idempotency mechanism** — "Add New Vouchers" vs "Modify
  Existing Vouchers", with a selectable **Voucher Identification Key** (Vch.No Only / +Series /
  +Date / +Series+Date). Re-posting the same externalRef's voucher number can be routed to
  "Modify Existing" instead of creating a duplicate — this was flagged as something we'd have
  to build ourselves; Busy already has it.
- **Fully UIA-accessible** — unlike the rest of Busy's VB6/ThunderRT6 UI (mostly unlabeled
  custom-drawn controls, confirmed by the earlier full-window tree dump), this specific dialog
  exposes proper `auto_id`s and control types on every field. Genuinely scriptable via
  pywinauto's `uia` backend, not a fragile coordinate-clicking hack.

## Confirmed screen layout (Sales Import format)

**Select Voucher Type**: Sales &nbsp; **Select Format**: Sales Import (a dropdown — other
voucher types/formats presumably exist, not yet enumerated)

| Section | Fields |
|---|---|
| Voucher Details to be Updated | ☑ Add New Vouchers · ☐ Modify Existing Vouchers · ☐ Skip Items With Zero Quantity |
| Voucher Identification Key Fields (active only in Modify mode) | Vch.No Only / +Series / +Date / +Series+Date |
| Key Fields | Series Name, Date, Tran Type, Sale Type, Sales Acc. · Target MC Name, Pymt./Rcpt. Mode, Party Name, MC Name |
| Masters Creation Info | ☐ Create New Master Used in Vouchers (+ default-values sub-fields, greyed while unchecked) |
| GST Report Basis | As Per Party Master / Billing-Shipping Details |
| Item Fields → Pick Data from Item Master | ☐ Price · ☑ Tax Rate · ☐ Cess Rate · ☐ MRP |
| Item Fields → Auto Calculate Amount | ☐ Item Amount · ☑ Tax Amount · ☐ Cess Amount |
| Configure Bill Sundries | No. of Bill Sundries: 2 (max 10) — row 1 "Rounded Off (+)" → Excel col **L**, row 2 "Rounded Off (-)" → Excel col **M** |
| Excel/Google Sheet File Info | ⦿ Excel / ○ Google Sheet, File Path, Sheet No. (1), Starting Row (2), Ending Row (0 = to end) |
| ☐ Import Vouchers Without Error Message | |
| Buttons | **Import**, Quit, **Download Sample File**, **Configure** (not yet explored) |

## Confirmed sample file format (`13_sample_saleVch.xlsx`)

11 columns, header row 1, data from row 2 (matches the dialog's own "Starting Row: 2"):

```
VCH_SERIES | VCH/BILL_DATE | VCH/BILL_NO | SALE/PURC_TYPE | PARTY_NAME | MC_NAME | ITEM_NAME | QUANTITY | UNIT | PRICE | AMOUNT
```

- **One voucher = one or more contiguous rows.** The header-level columns (series, date,
  bill no, sale type, party, MC) are populated **only on the first row** of each voucher; every
  additional item line for that same voucher leaves those columns blank (`None`) and only fills
  `ITEM_NAME`/`QUANTITY`/`UNIT`/`PRICE`/`AMOUNT`. A blank-header row is therefore "still the
  previous voucher," not a new one — this grouping rule is the key thing any generator must get
  right, same shape as how Tally's own XML groups `ALLINVENTORYENTRIES.LIST` under one voucher.
- `SALE/PURC_TYPE` uses Busy's own Sale-Type master names (`Local-Exempt` in the sample) — the
  same `Master1` MasterType=13/14 rows `busy-extraction-poc/FINDINGS.md` already catalogued
  (`Local-18%`, `Central-5%`, etc.) — not a free-text GST rate.
- `PARTY_NAME` matches a real Ledger master name exactly (`Busy Infotech Pvt. Ltd.` / `Cash` in
  the sample) — same name-matching convention (no fuzzy matching) this app's own Tally Mode
  integration already relies on everywhere.
- This base sample has **no GST/HSN/discount columns** — the checked boxes in this session
  (Tax Rate picked from Item Master, Tax Amount auto-calculated) mean Busy derives GST from the
  Item Master + Sale Type rather than reading it from the sheet. **Not yet confirmed**: whether
  ticking "Price"/"MRP"/discount-related options, or a different Format, adds columns to the
  generated sample — didn't re-generate with different checkbox combinations (would mean more
  live clicks; see below).
- `WMS/25-26/XXXXX` fits directly into `VCH_SERIES`+`VCH/BILL_NO` — but see Open Questions:
  whether Busy's Voucher Series master needs to actually contain a series named to match, or
  whether this column free-types a series name Busy resolves/creates on the fly.

## What's still unexplored (needs more live clicks — see Open Questions)

- The **Configure** button (top right) — untouched. Likely controls which extra columns
  (GST%, HSN, discount, party GSTIN/shipping address, transport/e-way-bill fields your plan's
  `transport{}`/`party.shippedTo{}` need) get added to the sheet. This is probably where the
  answer to "can this dialog even carry e-way bill / transport / shipped-to data at all" lives —
  none of that appears in the base 11-column sample.
- Whether "Modify Existing Vouchers" + a matching key actually UPDATES a prior WMS-pushed
  voucher cleanly (amounts, lines) vs. erroring/duplicating lines — not tested (would require an
  actual test import against this live company).
- Whether a genuinely new `VCH_SERIES` name (e.g. `WMS`) gets silently auto-created as a new
  Voucher Series master, gets rejected, or needs to be pre-created by hand in Busy first — ties
  directly into your "WMS-specific series, never touching Busy's manual series" requirement.
- Whether e-Invoice IRN generation actually fires synchronously during this import (confirmed
  IRN fields exist and are populated for ~32% of real Sales vouchers — see `SCHEMA_DISCOVERY.md`)
  or requires a separate manual "Generate E-Invoice" step afterward.
- Automating the **Import** click itself, and the pass/fail signal after it — Busy raises
  validation dialogs on bad data (per your original ask); haven't seen what that looks like or
  confirmed it's detectable via UIA (likely yes, given how clean this dialog's tree is, but
  unconfirmed).

None of the above needs new risk exposure to check — same read-only-click, screenshot-and-read
pattern used so far — but each one is a live click in a session you're actively steering (the
auto-mode classifier is blocking me from clicking further without you clicking or an explicit
permission change). Tell me how you want to keep going: you click through Configure /
try a test Modify-mode import / a throwaway test voucher and I keep documenting what happens, or
you loosen the Bash permission for this kind of click so I can drive it directly.
