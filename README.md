# Busy Voucher Push

A standalone JSON API service that takes a sales voucher from **your own
ERP/WMS** and pushes it into **Busy Accounting Software** — with no manual
re-typing, and without touching Busy's database directly. It works by
generating the exact `.xlsx` file Busy's own **"Import Vouchers From
Excel/Google Sheet"** dialog expects, then (optionally) driving that dialog
itself via Windows UI automation, so the whole thing runs unattended from
one API call.

This is one of three separate Busy-integration approaches used in this
codebase family — see [`../docs/COPY_FOR_BUSY_PATTERN.md`](../docs/COPY_FOR_BUSY_PATTERN.md)
(a much simpler copy/paste helper for when a human is entering the voucher
by hand anyway) and the separate `busy-data-extraction` repo (pulls data
OUT of Busy — the opposite direction). Pick this one specifically when your
goal is "push a voucher into Busy with no human re-typing it."

## Why it works this way (read this before changing the approach)

Busy has **no public write API**. Writing directly into its SQL tables was
tried first and deliberately abandoned: one voucher touches over 100
columns across two tables, several with unconfirmed write-time semantics
(auto-numbering races, e-Invoice IRN generation, balance-tracking fields
that Busy itself maintains) — get any of that wrong and you get a voucher
that "looks fine" but is subtly broken in Busy's own eyes. Driving Busy's
**own Import-from-Excel dialog** avoids all of that, because Busy's own
save code does the actual writing — this tool only ever prepares an input
file and clicks Busy's own buttons, the same as a human would.

**Practical implication**: this only works on a Windows machine with a
licensed Busy install actually running and logged in — it cannot run
headless on a Linux server, and it cannot run against a Busy install this
machine can't see on screen.

## Architecture, end to end

```
Your ERP  --POST /vouchers (JSON)-->  this service
                                         |
                                         |-- writes outbox/<vch_no>_<ts>.xlsx
                                         |   (exact column layout Busy's
                                         |    Import dialog is configured
                                         |    to read -- see below)
                                         |
                                         '-- if Auto-import is ON:
                                             drives Busy's own Import dialog
                                             via pywinauto (Windows UI
                                             automation) and reports back
                                             the REAL outcome
```

There is **no live-DB dependency anywhere in this pipeline** — every
earlier version of this tool tried validating the voucher against Busy's
SQL Server first (does this party/item/sale-type actually exist?); all of
that was deliberately removed. Busy's own Import dialog is now the *only*
validator, for every install, no exceptions — the same as if a human had
typed the voucher in by hand and clicked Import themselves.

## Requirements

- Windows, with **Busy** installed, running, and logged into the correct
  company — and left running (this tool drives its existing window, it
  doesn't launch Busy itself).
- Python 3.11+.
- A **dedicated voucher series in Busy**, created specifically for this
  integration, with **Numbering Type = Manual** and **Duplicate/Blank
  Voucher Number = Don't Allow**. Do not point this at a series real staff
  also type into by hand — see "Voucher numbering" below for why.
- Busy's own **Import Vouchers From Excel/Google Sheet** dialog (Busy menu:
  Administration → Data Export/Import) must already be Configured (Busy's
  own one-time "Configure" step inside that dialog) to accept the column
  layout this tool writes — see "The Excel column layout" below.

## Setup

```bash
cd busy-voucher-push
python -m venv venv
venv\Scripts\pip install -r requirements.txt   # fastapi, uvicorn, openpyxl, pywinauto, pydantic
copy config.json config.local.json             # if you need machine-specific overrides; usually unnecessary now
venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Open `http://<this-machine>:8010/settings` in a browser — this is where you
tell the tool which Busy Voucher Type/Format/checkbox state to use (see
"The Settings GUI" below). **Do this before your first real push.**

## The Excel column layout

Busy's Import dialog is configured (a one-time, human, in-Busy step on
Busy's own "Configure" screen inside the Import dialog) to expect exactly
these 11 columns, with header fields (series/date/number/sale
type/party/material centre) only populated on each voucher's FIRST item
row, and left blank on subsequent item rows of the same voucher:

| Column | Meaning |
|---|---|
| `VCH_SERIES` | The voucher series (must be your dedicated Manual-numbered series) |
| `VCH/BILL_DATE` | Voucher date |
| `VCH/BILL_NO` | The voucher number — supplied by YOUR system, never auto-allocated here (see below) |
| `SALE/PURC_TYPE` | Must be a real Sale-Type master name already configured in Busy |
| `PARTY_NAME` | Must be a real Party/Ledger master name already in Busy |
| `MC_NAME` | Material Centre / Godown name |
| `ITEM_NAME` | Must be a real Stock Item master name already in Busy |
| `QUANTITY` | |
| `LIST_PRICE` | |
| `AMOUNT` | Whether this is read as tax-inclusive or tax-exclusive depends entirely on the Sale Type you use — **test this against your own Busy install's own Sale Type masters before trusting it**, don't assume |
| `DISCOUNT_PERCENT` | |

**This layout is specific to how this Busy install's Import dialog was
Configured.** If your Busy's Configure screen maps different columns (or
you add GST%/HSN/party-GSTIN/e-way-bill fields, which Busy's field catalog
supports but this default mapping doesn't use), update
`app/excel_writer.py`'s column list and `app/voucher_model.py`'s fields to
match — and re-verify with a real test import before trusting it.

## Voucher numbering — you own it, this tool doesn't

`vch_no` is a **required** field on every push — there is no
auto-allocation, no local duplicate check, and no idempotency store in this
tool. It's written into the `.xlsx` exactly as given. The only thing
standing between an accidental double-push and a real duplicate voucher in
Busy is **Busy's own numbering-mode enforcement** on your dedicated series
(Duplicate Voucher Number = Don't Allow) — which is exactly why the setup
step above insists on that series configuration. If you want retry-safety
or dedup, build it in your OWN system before calling this API (e.g. don't
call `POST /vouchers` twice for the same order), not here.

## The Settings GUI (`/settings`)

Busy's Import dialog carries several on-screen settings (Voucher Type,
Format Name, and ~9 checkboxes like "Skip Items With Zero Quantity"/"Create
New Master Used in Vouchers") that **another person using the same Busy
install can change** just by clicking around on a different screen — this
was hit live, repeatedly, during development. Hardcoding "known-good"
values in code meant every drift needed a code change to fix. Instead,
every run **self-heals**: before each import, this tool reads the dialog's
actual current state and only clicks something if it's already wrong,
correcting it back to whatever is saved in `/settings` (a small sqlite
file, survives restarts). If your Busy install's correct values drift or
differ from the defaults, fix them on this page — never in code.

Also on this page: **Auto-import on push** — off by default. With it off,
`POST /vouchers` only generates the `.xlsx` file (in `outbox/`) and someone
imports it into Busy manually later. With it on, the push ALSO drives
Busy's dialog and clicks Import for real, and the API response reports the
genuine outcome.

## The `/push` page

A small manual-testing UI (`GET /push`) — paste/edit a voucher JSON payload,
click "Push Voucher", see the real result. Sample payloads are one click
away. Useful for a first supervised test before wiring up your own ERP's
automated calls. Shows a loading overlay while a push is in flight, since a
real Busy import can take up to ~30 seconds.

## `POST /vouchers` — the integration surface

```json
{
  "series": "ZMANUAL",
  "vch_date": "2026-09-18",
  "party_name": "Some Real Busy Party Name",
  "sale_type": "Local-TaxIncl.",
  "mc_name": "Main Store",
  "vch_no": "ZMANUAL/26-27/00042",
  "items": [
    { "item_name": "SOME-REAL-SKU", "quantity": 2, "list_price": 450, "amount": 900, "discount_percent": 0 }
  ]
}
```

`series` must be one of the series explicitly allow-listed in
`app/voucher_model.py::ALLOWED_SERIES` (a deliberate safety rail — this
tool will never write into a series real staff also type into by hand).
Every other field is passed through as-is with no live-DB check — a typo'd
party/item/sale-type name is **not** caught here, it's caught (or not) by
Busy's own Import dialog when the file is actually imported.

**Response** (`status` field is the important one):

| `status` | Meaning |
|---|---|
| `FILE_READY_NOT_IMPORTED` | Auto-import is off — the `.xlsx` was generated, nobody's imported it yet |
| `SUCCESS` | Auto-import is on and Busy confirmed the import succeeded |
| `NEEDS_HUMAN_ATTENTION` | Auto-import ran but hit an unrecognized popup/dialog state — check `new_window_title`/`new_window_message`/`new_window_buttons` in the response and go look at the Busy window yourself |
| `AUTOMATION_ERROR` | An exception happened during automation (e.g. Busy wasn't in the expected state) — the `.xlsx` file still exists in `outbox/` even though the push didn't complete |

**Every `apiFetch`/HTTP call your own system makes against this API should
have real error handling** — a network failure, a validation 422, or a
`NEEDS_HUMAN_ATTENTION`/`AUTOMATION_ERROR` status are all real outcomes
your integration needs to handle, not edge cases to ignore.

## Samples

`samples/*.json` — ready-to-push example payloads, also selectable
directly from the `/push` page. **Change `vch_no` before reusing one** —
since there's no dedup layer here, pushing the same sample twice with the
same `vch_no` will either produce two `.xlsx` files for the same number
(harmless, nothing's imported yet) or, if Auto-import is on, get rejected
by Busy's own duplicate-number check on the second attempt.

## Testing

```bash
venv\Scripts\python -m pytest -q tests
```

These tests never touch a real Busy window or a real database — they
exercise the FastAPI endpoints, the `.xlsx` column layout, and the
settings/idempotency-store isolation (an autouse fixture in
`tests/conftest.py` redirects every sqlite path to a temp file per test, so
running the suite can never corrupt your real `data/settings.sqlite`).

## What's NOT here, on purpose

- **No masters-existence validation.** An unknown party/item/sale-type name
  is not caught before Busy's own Import click. If you want that kind of
  safety net, validate it in YOUR OWN system (you presumably already know
  your own party/item master lists) before calling this API.
- **No retry/idempotency/dedup layer.** See "Voucher numbering" above.
- **No GST%/HSN/party-GSTIN/e-way-bill fields** on the default model — Busy
  supports them, this default column mapping doesn't carry them. Add them
  yourself (Configure screen in Busy + `app/voucher_model.py` +
  `app/excel_writer.py`) if you need them.
- **MS Access-backed Busy installs**: this tool no longer has ANY live-DB
  dependency at all (SQL Server or otherwise), so it works identically
  regardless of what database Busy itself uses underneath — the `.xlsx`
  generation and UI automation don't care.

## Adapting this for your own Busy install

1. Open Busy's own Import Vouchers From Excel dialog → Configure, and note
   its exact column mapping (or set one up matching the table above).
2. Create your own dedicated Manual-numbered voucher series (don't reuse
   an existing one staff type into by hand).
3. Update `app/voucher_model.py::ALLOWED_SERIES` to your series name(s).
4. Push a first test voucher with Auto-import OFF, confirm the generated
   `.xlsx` in `outbox/` looks right, then manually import it into Busy
   yourself once to confirm the mapping is correct end to end.
5. Only then turn Auto-import on via `/settings`, and treat the very first
   armed automated run as a supervised test (watch the Busy window while
   it happens), not routine unattended use.

## Repo layout

```
app/            FastAPI service — voucher model, Excel writer, Busy UI automation, settings store
tests/          pytest suite (no live Busy/DB dependency)
samples/        example JSON payloads
scripts/        one-off pywinauto inspection helpers used during development
config.json     template config (backend/db fields are vestigial — see DEV_NOTES.md)
DEV_NOTES.md    the original chronological build diary — historical context only, not a guide
```

`findings/`, `outbox/`, `data/` are local-machine-only working directories
(gitignored) — not part of the repo.
