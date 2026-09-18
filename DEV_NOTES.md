> **This is the original build-diary/status log, kept for historical
> context.** For the actual integration instructions, read `README.md`
> instead — this file is chronological dev notes, not a guide.

# Busy Voucher Push — status

**MILESTONE (2026-09-18): gates closed, fully unattended pipeline validated
twice.** `POST /vouchers` → preflight → generated `.xlsx` →
`app/busy_automation.py::run_one_import()` (opens dialog via Ctrl+W, sets
fields, clicks Import, auto-confirms both known gates, detects the real
"Vouchers Import!" success popup and dismisses it) now runs as ONE command
with no manual clicks, and reports `{"status": "SUCCESS", ...}`. Verified
against the live database twice in a row: `VchCode=7683`
(`ZManual/26-27/00012`, ₹50.00) and `VchCode=7684`
(`ZManual/26-27/00013`, ₹30.00), both exact matches to the pushed data.
First proof-of-concept run (manual gate clicks) was `VchCode=7681`
(`ZManual/26-27/00010`, ₹100.00).

Getting to fully-unattended surfaced 3 real bugs in the automation code
itself, each fixed and re-verified live (not just reasoned about):
1. `open_import_dialog()` crashed (`ElementNotEnabled`) if the dialog was
   already open from a prior run — fixed to detect and reuse it.
2. `wait_for_outcome()` took its "known popups" baseline snapshot AFTER
   `click_import()` had already fired, so an instantly-appearing popup was
   wrongly treated as pre-existing and silently missed. Fixed by moving the
   snapshot to before the click, in `run_one_import()`.
3. Popups from `main.descendants()` are plain `UIAWrapper` objects with no
   `child_window()` method (that only exists on `WindowSpecification`
   objects) — `AttributeError` crashed the first real run. Fixed with a
   `_click_button_in()` helper that walks `descendants(control_type="Button")`
   directly.
A prior run's un-dismissed success popup also silently blocked the NEXT
run's dialog entirely (`ElementNotEnabled` again) — reinforces why
`wait_for_outcome()` must always run to a real terminal state, never leave
a popup hanging.

**Real GST-taxed voucher confirmed working (2026-09-18), `VchCode=7687`
(`ZManual/26-27/00015`).** Two real findings from this:
1. **Sale Type `Local-18%` does NOT compute tax on this import path —
   `Local-TaxIncl.` does.** First attempt with `Local-18%` succeeded with
   zero errors but produced a voucher with `GSTInfo=False` and no CGST/SGST
   legs at all (confirmed via direct DB read, not just trusting the "no
   error" result). The fix (`Local-TaxIncl.`) came from the user checking
   Busy's own "Add Sales Voucher" screen directly, not from further
   automated trial-and-error. With it: `CGST Output`/`SGST Output` legs
   appear correctly (68.64 each, 18% split), `Sales` leg carries the
   correct ex-tax base (762.72), header `VchAmtBaseCur` carries the
   tax-inclusive total (900.00) — confirming `Local-TaxIncl.` treats the
   `AMOUNT` column as tax-INCLUSIVE and reverse-calculates the ex-tax
   base + GST split, not the other way round. `DISCOUNT_PERCENT` (10%)
   also carried through correctly onto the item leg (`D9=10.0`) in the
   same run. **`GSTInfo` stays `False` even on a genuinely-taxed voucher**
   — a known cosmetic quirk of this import path, not something blocking
   real GST reporting (the actual CGST/SGST ledger legs are what
   downstream reports/filing depend on).
2. **Voucher-Type/Format drift is a recurring, not one-off, problem** —
   happened twice, both times after using a different Busy screen in
   between runs (not caused by this code). Fixed properly this time:
   `ensure_voucher_type_and_format()` checks the current value via the
   `Select Voucher Type`/`Select Format` Edit boxes (readable even with
   the dropdown closed) and only touches the dropdown if it's already
   wrong — wired into `run_one_import()` so every run self-heals rather
   than trusting remembered dialog state. Confirmed live: caught and fixed
   a real "Sales Order"/blank-Format drift automatically before proceeding
   to a successful import.

**Decision (2026-09-18): direct-SQL insert is parked. Excel-import via
"Import Vouchers From Excel/Google Sheet" (Administration → Data Export
Import) is the production path.** Reason: one voucher touches 123 `Tran1` +
up to 5×104 `Tran2` columns, several with unconfirmed write-time semantics
(`Balance1-3`, `ItemBal1-3`, `CFMode`, etc. — see
`findings/SQL_PREFLIGHT_NOTES.md`). The Excel-import path avoids all of that
by going through Busy's own real save code. `busy_wms_writer` (the
write-capable SQL login) and `app/sql_preflight.py` still exist and still
work (verified live) but are no longer on the critical path — `sql_preflight.py`'s
master/duplicate-existence checks are still useful validation logic and
could be reused ahead of an Excel-import push too. See
`scripts/drop_write_login.sql` if you want to remove the now-unused login.

WMS → Busy ERP sales-voucher push utility. Built in this folder, separate from
the main `warehouse-system` app (per CLAUDE.md this integration isn't part of
the documented app yet — see `findings/` for why direct-SQL writes are on
hold and the Excel-import path was chosen instead).

**Nothing here writes into the live Busy database or drives Busy's UI.**
Everything below stops at "here's a file, go import it yourself."

## Database backend (2026-09-18 revision): SQL Server only, by explicit choice

**Earlier revision of this section described a "backend-agnostic" SQL Server
vs. MS Access connection layer that ran the SAME queries against either.
That was wrong and has been reverted for the Access case.** We have zero
verified knowledge of the Access-backed Busy install's actual table/column
schema — pretending the SQL Server queries (`Tran1`/`Tran2`/`Master1`, the
specific column names/joins confirmed in `findings/SQL_PREFLIGHT_NOTES.md`)
would work unchanged against it was an unverified assumption stated as if
it were a design decision. Fixed properly instead:

- **`settings_store.py`'s `db_backend` setting** (`"sqlserver"` /
  `"access"` / `"none"`, editable via the `/settings` page's own
  "Database backend" card — an explicit human choice, never inferred) is
  now the ONE switch controlling whether `sql_preflight.py` is used AT
  ALL.
- **`db_backend != "sqlserver"` means preflight validation (masters
  existence, duplicate-number check) is SKIPPED entirely** — not
  attempted-then-caught, not run-against-possibly-wrong-queries — genuinely
  never called. `idempotency.py`'s self-healing counter lookup
  (`_highest_existing_no`) is gated the same way. Verified live:
  `test_no_db_validation_when_backend_is_not_sqlserver` pushes a voucher
  with a deliberately nonexistent party/item/sale-type against
  `db_backend="access"` and confirms it's accepted (file generated, no
  rejection) rather than silently checked against irrelevant queries.
- **The response always says so explicitly** — `preflight` field reads
  `"SKIPPED -- db_backend is 'access', no verified schema to validate
  against..."` rather than the SQL-Server path's `"PASSED"`, so a caller
  can never mistake "we didn't check" for "we checked and it's fine".
- With `db_backend` set to anything but `"sqlserver"`, **Busy's own Import
  dialog is the only validation a push gets** — exactly the same as if
  someone had typed the voucher in by hand. This is the honest, current
  state of MS Access support: config plumbing exists (`config.json`'s
  `access.file_path`, `sql_preflight.py`'s connection-string branch) for
  whenever someone actually maps that schema, but until then it's
  deliberately inert, not silently wrong.

## Third-party integration (2026-09-18)

**`POST /vouchers` is the stable integration surface** — any external system
sends JSON (see `app/voucher_model.py` for the schema), gets back a real
status. If "Auto-import on push" is on (see Settings below), the response's
`status` reflects the REAL Busy outcome (`SUCCESS`/`NEEDS_HUMAN_ATTENTION`/
`DRY_RUN`), not just "file generated" — the caller doesn't need to know
anything about Busy's dialog, field mapping, or UI quirks; those are this
service's problem.

**Settings GUI**: `GET /settings` (HTML form, `POST` to save) — edits the
persisted "known-good" dialog config (`app/settings_store.py`, sqlite-backed,
survives restarts): Voucher Type, Format Name, an Automation-armed toggle
(replaces the `BUSY_AUTOMATION_ARMED` env var as the durable default — the
env var still overrides it when set, useful for a single supervised test
run), and Auto-import-on-push. Exists because **other Busy users can change
on-screen settings between pushes** (confirmed live, twice — Voucher
Type/Format drifted away from Sales/Sales Import) — fixing a future drift to
a genuinely different correct value is now a settings-page edit, never a
code change.

**Clean-slate reset**: `busy_automation.py::reset_busy_to_clean_state()`
closes every stray child window under Busy's main window (leftover popups
AND the Import dialog itself, if left open) before every `run_one_import()`
call — confirmed necessary live (a left-open success popup silently blocked
the next run's dialog). Guarantees every run starts from the same clean
state regardless of what a prior run or another user's session left behind.

## What works today

```
busy-voucher-push/venv/Scripts/python.exe -m pytest -q tests   # 23/23 passing
busy-voucher-push/venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

**2026-09-18: `external_ref` and auto-allocation removed entirely, per
explicit request ("remove ext ref").** `POST /vouchers` (JSON only — see
`app/voucher_model.py`) requires `vch_no` on every voucher — no
idempotency store, no auto-numbering, no local or remote duplicate check
anywhere in this tool. `vch_no` is used exactly as given and written
straight into the `.xlsx` in `outbox/`. `app/idempotency.py` is kept in the
repo (not deleted) but is now unused dead code. `GET /vouchers/{external_ref}`
was removed along with it — there's no longer anything to look up.

The generated `.xlsx` matches the EXACT column layout confirmed live against
Busy's own "Import Vouchers From Excel" (Sales Import format) Configure
screen on this install — see `findings/EXCEL_IMPORT_DIALOG.md`.

## What's NOT built yet, on purpose

- **The actual Busy-side import automation** (`app/busy_automation.py`) is
  fully scoped and coded, including opening the dialog completely
  unattended — safety-gated behind `BUSY_AUTOMATION_ARMED=1` (unset by
  default; every UI-driving function no-ops/dry-run-logs without it,
  `click_import()` hard-refuses outright). Read-only functions
  (`find_dialog`, `inspect_fields`) are safe any time.
  - **Dialog now opens unattended**: the user created a Busy keyboard
    shortcut (Ctrl+W, via Busy's own "Create Shortcut" on the
    Administration menu) that opens "Import Vouchers From Excel" directly
    — confirmed live via `open_import_dialog()`. This was the one real gap
    left (Busy's menu bar itself has no real Win32 `HMENU` and no UIA
    `TreeItem` elements — fully custom-drawn, not automatable the way the
    dialog's own internal controls are) and it's now closed.
  - **`run_one_import(xlsx_path, mode)`** is the full pipeline: open
    dialog → set file path → set mode → click Import → report outcome.
    Dry-run tested end-to-end (prints every step, touches nothing). NOT
    yet tested armed/live — the individual pieces are proven (dialog
    opens via shortcut, field reads are correct, one manual Import
    already succeeded once) but this exact function has never run
    start-to-finish for real. Treat the first armed run as a supervised
    test, not routine use.
  - **Real popups now confirmed, 3 so far** (all are children of the MAIN
    window, several nested under the dialog itself, NOT top-level Desktop
    windows — `wait_for_outcome()` was fixed twice to account for this,
    first `children()`→broken because a popup can nest under the dialog not
    main directly, then fixed to `descendants()`, recursive):
    1. `" Invalid Format !"` (leading space in the title) — informational,
       OK button only. Caused by "Select Voucher Type"/"Select Format"
       having drifted away from Sales/Sales Import (leftover from earlier
       manual exploration, not something this code causes) — fixed by
       selecting the real `ListItem` controls for "Sales"/"Sales Import"
       (confirmed genuinely UIA-accessible when the dropdown is open,
       unlike the rest of Busy's menus).
    2. `"Start Data Import !"` — a real Yes/No confirmation gate BEFORE
       every import actually runs ("Import process is going to start. Do
       you wish to continue?"). Not yet auto-answered by `click_import()` —
       currently a second manual/explicit step.
    3. `"Invalid Data !"` — *"Vouchers can not be imported if voucher
       numbering is Automatic or Not Required. Set voucher numbering to
       Manual."* Appeared even against our confirmed-Manual `ZManual`
       series; clicking its own Yes proceeded and the import succeeded —
       exact meaning/scope of this check (series-level vs. voucher-type/
       format-level default) still not fully understood, but empirically
       safe to click Yes on for this series. Needs revisiting before
       treating "click Yes" here as a safe default for every future
       series/voucher type.
    `wait_for_outcome()` correctly detects all 3 by title now
    (`NEEDS_HUMAN_ATTENTION`), but does not yet auto-answer any of
    them — every real run so far needed a human to click through gates
    2 and 3. Still not seen: a genuine "N vouchers imported successfully"
    confirmation with no further prompts, or what a real error looks like
    when it's about the DATA (bad item/party) rather than dialog
    configuration (all our earlier bad-data tests were caught by
    `sql_preflight.py` before ever reaching Busy's own Import click).
- **GST%/HSN/party-GSTIN/transport/e-way-bill fields.** Busy's field catalog
  has all of these (confirmed via the Configure field picker — VAT/GST,
  Bill Reference, Challan/Orders/Quotations/Indent categories) but this
  install's *current* Configure mapping doesn't include them. Needs a
  Configure-screen change in Busy (another live click) before this model can
  carry them — see Open Questions in `findings/EXCEL_IMPORT_DIALOG.md`.
- **XML fallback path** — not started.
- **Direct-SQL path** — see `findings/SCHEMA_DISCOVERY.md`. On hold pending:
  (1) a real answer on the voucher-number race condition (no DB-level
  uniqueness on `VchSeriesCode+AutoVchNo`), (2) confirming whether e-Invoice
  IRN generation can be triggered/skipped safely for WMS-originated vouchers
  (confirmed active on this GSTIN — ~32% of this FY's real Sales vouchers
  carry a real IRN).
- **Write-capable SQL login** — not created. Current SQL access is still the
  pre-existing read-only `busy_sync_reader` (reused from `busy-extraction-poc/`).

## Folder layout

```
app/            FastAPI ingestion endpoint, voucher model, Excel writer, idempotency store
tests/          pytest round-trip tests (column layout, grouping, idempotency)
scripts/        pywinauto inspection scripts (read-only tree/screenshot dumps)
findings/       schema discovery + live UI dialog mapping, numbered raw query dumps
outbox/         generated .xlsx files awaiting manual import (gitignored)
data/           idempotency.sqlite (gitignored)
```

## Next decision point

Tell me which to do next:
1. You do one manual test import (generate a file via the API, import it
   yourself in Busy, tell me what happened — numbering/e-Invoice/GST correct?)
   before any Import-click automation is attempted.
2. Open Busy's Configure screen again and add GST%/HSN/party-GSTIN columns to
   the mapping so the voucher model can be extended to carry them.
3. Revisit the direct-SQL path's two open questions instead (sp_getapplock
   design + confirming e-Invoice behavior) rather than leaning fully on the
   Excel-import path.
