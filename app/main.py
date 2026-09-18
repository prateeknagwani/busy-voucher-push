"""Busy Voucher Push -- the JSON API third-party systems integrate against.

POST /vouchers accepts ONE WmsVoucher (vch_no is REQUIRED -- no
external_ref, no auto-allocation, no idempotency store; removed
2026-09-18, per explicit request -- see voucher_model.py). NO live-DB
validation happens anywhere in this pipeline (the masters-existence
preflight check was ALSO removed 2026-09-18, same day and same reasoning
as the duplicate-number check before it -- "let Busy handle it", now fully
consistent across every db_backend rather than sqlserver behaving
differently from access/none). The .xlsx is written straight from the
JSON payload; Busy's own Import dialog is the only validation any push
ever gets, same as if someone had typed the voucher in by hand. If the
"Auto-import on push" setting is on (see /settings), it then immediately
runs the real Busy-side import too (app/busy_automation.py::run_one_import)
and the response reflects the REAL outcome (SUCCESS / NEEDS_HUMAN_ATTENTION
/ DRY_RUN if automation isn't armed) -- not just "file generated".

This is the stable integration surface: a calling system sends JSON, gets
back a real status. It does not need to know anything about Busy's own
Excel-import dialog, field mapping, or UI quirks -- those are this
service's problem, encapsulated behind app/busy_automation.py and kept
self-healing (see reset_busy_to_clean_state/ensure_voucher_type_and_format)
specifically because other Busy users can and do change on-screen settings
between pushes.

Production path is Excel-import (Administration > Data Export Import >
Import Vouchers from Excel/Google Sheet) -- see README.md for why the
direct-SQL insert path was parked instead.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from . import settings_store
from .excel_writer import write_vouchers_xlsx
from .voucher_model import WmsVoucher

OUTBOX_DIR = Path(__file__).resolve().parent.parent / "outbox"

app = FastAPI(title="Busy Voucher Push")


def _push_voucher_core(voucher: WmsVoucher) -> tuple[bool, dict]:
    """Shared logic behind both POST /vouchers (JSON API) and the /push UI
    form -- one implementation, two front doors. Returns (ok, response).

    No external_ref, no idempotency store, no preflight validation of any
    kind (masters-existence check removed 2026-09-18, same day/reasoning as
    the duplicate-number check before it -- "let Busy handle it", now
    fully consistent regardless of db_backend). voucher.vch_no is used
    exactly as given. Busy's own Import dialog is the only validation any
    push ever gets."""
    candidate_vch_no = voucher.vch_no
    preflight_note = "SKIPPED -- no live-DB validation of any kind; Busy's own Import dialog is the only validation this push gets."

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_vch_no = candidate_vch_no.replace("/", "-").replace(" ", "")
    out_path = OUTBOX_DIR / f"{safe_vch_no}_{ts}.xlsx"
    write_vouchers_xlsx([voucher], out_path)

    response = {
        "busy_vch_no": candidate_vch_no,
        "xlsx_path": str(out_path),
        "preflight": preflight_note,
    }

    if settings_store.get_bool("auto_import_on_push"):
        from .busy_automation import run_one_import  # deferred: only needed if actually importing

        try:
            import_result = run_one_import(out_path, mode="ADD")
        except Exception as e:
            # The .xlsx WAS already generated successfully at this point --
            # a UI-automation failure (Busy not in the expected state, a
            # control not found, etc.) must not look like the whole push
            # failed. Real bug hit live: an uncaught pywinauto TimeoutError
            # here 500'd the entire request instead of reporting this.
            import_result = {
                "status": "AUTOMATION_ERROR",
                "error": f"{type(e).__name__}: {e}",
            }
        response["import_result"] = import_result
        response["status"] = import_result.get("status", "UNKNOWN")
    else:
        response["status"] = "FILE_READY_NOT_IMPORTED"
        response["note"] = (
            "Auto-import on push is OFF (see /settings). Import this file yourself via "
            "Administration > Data Export Import > Import Vouchers from Excel/Google Sheet "
            "(Sales Import format), or turn Auto-import on."
        )

    return True, response


@app.post("/vouchers")
def push_voucher(voucher: WmsVoucher):
    ok, response = _push_voucher_core(voucher)
    if not ok:
        raise HTTPException(status_code=422, detail=response)
    return response


# --- Settings GUI -----------------------------------------------------
# Plain server-rendered HTML, no build step / JS framework -- this is a
# small internal admin page, not a customer-facing product surface. Exists
# because "other users might change Busy settings" (e.g. Voucher Type/
# Format drift, confirmed live) means the known-good values need to be
# editable data, not a hardcoded literal requiring a code change to fix.

_DB_BACKEND_OPTIONS = [
    ("sqlserver", "SQL Server (tested)"),
    ("access", "MS Access (schema unverified — validation skipped)"),
    ("none", "No database configured (validation skipped)"),
]

_SETTINGS_FIELDS = [
    ("voucher_type", "Voucher Type", "Must exactly match a real Busy Voucher Type name, e.g. 'Sales'."),
    ("format_name", "Format Name", "Must exactly match a real Busy Format name for that Voucher Type, e.g. 'Sales Import'."),
    ("default_mode", "Default mode", "'ADD' or 'MODIFY' -- what a push uses when it doesn't specify one. Add New/Modify Existing checkboxes are always explicitly set to match, every run, so they can't drift the way the other checkboxes below can."),
    ("gst_report_basis", "GST Report Basis", "'As Per Party Master' or 'Billing/Shipping Details'. Confirmed grayed out/disabled on every screenshot so far -- kept as a setting so it self-heals automatically if it's ever found selectable."),
]
_SETTINGS_BOOLS = [
    ("automation_armed", "Automation armed", "When ON, busy_automation.py's UI-driving functions actually click/type in Busy. When OFF, everything dry-runs (prints only)."),
    ("auto_import_on_push", "Auto-import on push", "When ON, POST /vouchers also runs the real Busy import immediately, not just file generation."),
]

_CHECKBOX_SETTINGS_BOOLS = [
    ("skip_items_zero_qty", "Skip Items With Zero Quantity", ""),
    ("create_new_master", "Create New Master Used in Vouchers", ""),
    ("pick_price", "Pick Data from Item Master → Price", ""),
    ("pick_tax_rate", "Pick Data from Item Master → Tax Rate", "Confirmed ON is required for real GST computation to work."),
    ("pick_cess_rate", "Pick Data from Item Master → Cess Rate", ""),
    ("pick_mrp", "Pick Data from Item Master → MRP", ""),
    ("auto_item_amount", "Auto Calculate Amount → Item Amount", ""),
    ("auto_tax_amount", "Auto Calculate Amount → Tax Amount", "Confirmed ON is required for real GST computation to work."),
    ("auto_cess_amount", "Auto Calculate Amount → Cess Amount", ""),
]


# Mirrors the real warehouse-system frontend's tokens.css / index.css
# conventions (navy page titles, .card sections, .btn-primary/.btn-outline,
# the app's exact color/spacing/radius scale) so this admin page reads as
# part of the same product, not a bolted-on dev tool -- even though it's a
# plain server-rendered page (no React/build step; not worth one for a
# single internal settings screen).
_PAGE_STYLE = """
:root {
  --navy: #0f2744; --accent: #2563eb; --accent-tint: #eff6ff;
  --surface: #ffffff; --surface-muted: #f8fafc; --bg: #f1f5f9;
  --border: #e2e8f0; --text: #0f172a; --muted: #64748b;
  --green: #16a34a; --green-bg: #dcfce7; --green-border: #86efac; --green-text: #14532d;
  --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --radius-md: 10px; --radius-sm: 6px;
  --shadow-sm: 0 1px 2px rgba(15,23,42,0.06);
}
body { margin: 0; font-family: var(--font); background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased; }
.page { max-width: 760px; margin: 40px auto; padding: 0 16px; }
.page-header { margin-bottom: 4px; }
.page-title { margin: 0; font-size: 22px; font-weight: 700; color: var(--navy); letter-spacing: -0.02em; }
.page-header-subtitle { color: var(--muted); font-size: 13px; margin: 6px 0 20px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 20px; margin-bottom: 16px; box-shadow: var(--shadow-sm); }
.card h2 { margin: 0 0 4px; font-size: 15px; font-weight: 700; color: var(--navy); }
.card .card-help { color: var(--muted); font-size: 13px; margin: 0 0 16px; }
.form-row { display: flex; align-items: flex-start; gap: 16px; padding: 10px 0; border-bottom: 1px solid #f1f5f9; }
.form-row:last-child { border-bottom: none; }
.form-row label { flex: 0 0 260px; font-size: 14px; font-weight: 600; color: var(--text); padding-top: 8px; }
.form-row .field { flex: 1; }
.form-row input[type=text], .form-row select { width: 100%; box-sizing: border-box; padding: 8px 10px; font-size: 14px; font-family: var(--font); border: 1.5px solid var(--border); border-radius: var(--radius-sm); color: var(--text); background: var(--surface); }
.form-row input[type=text]:focus, .form-row select:focus { outline: none; border-color: var(--accent); }
.form-row input[type=checkbox] { width: 18px; height: 18px; margin-top: 6px; }
.form-row .help { color: var(--muted); font-size: 12px; margin-top: 4px; }
.btn { display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 10px 22px; font-size: 15px; font-weight: 600; font-family: var(--font); border: 1.5px solid transparent; border-radius: var(--radius-md); cursor: pointer; color: #fff; background: var(--accent); }
.btn:hover { filter: brightness(0.95); }
.banner-saved { background: var(--green-bg); border: 1px solid var(--green-border); color: var(--green-text); border-radius: var(--radius-sm); padding: 10px 14px; font-size: 14px; font-weight: 600; margin-bottom: 16px; }
"""


def _settings_page_html(saved: bool = False) -> str:
    values = settings_store.get_all()
    banner = '<div class="banner-saved">Saved.</div>' if saved else ""

    def _text_rows(fields):
        return "".join(
            f"""<div class="form-row">
                <label for="{key}">{html.escape(label)}</label>
                <div class="field">
                    <input type="text" id="{key}" name="{key}" value="{html.escape(values[key])}">
                    {f'<div class="help">{html.escape(help_text)}</div>' if help_text else ""}
                </div>
            </div>"""
            for key, label, help_text in fields
        )

    def _bool_rows(fields):
        return "".join(
            f"""<div class="form-row">
                <label for="{key}">{html.escape(label)}</label>
                <div class="field">
                    <input type="checkbox" id="{key}" name="{key}" value="1" {"checked" if values[key] == "1" else ""}>
                    {f'<div class="help">{html.escape(help_text)}</div>' if help_text else ""}
                </div>
            </div>"""
            for key, label, help_text in fields
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<title>Busy Voucher Push — Settings</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_PAGE_STYLE}</style>
</head>
<body>
<div class="page">
  <div class="page-header">
    <h1 class="page-title">Busy Voucher Push — Settings</h1>
  </div>
  <p class="page-header-subtitle">Known-good values busy_automation.py resets Busy's Import dialog to before every run — edit here when a real Busy setting changes, no code change needed.</p>

  {banner}

  <form method="post" action="/settings">

    <div class="card">
      <h2>Database backend</h2>
      <p class="card-help">Which real database this Busy install actually uses. Controls whether preflight validation (masters/duplicate-number check) runs at all — it is NEVER attempted for MS Access or "no database", since that schema has not been verified. Kept as its own separate, explicit choice rather than guessed from anything else.</p>
      <div class="form-row">
        <label for="db_backend">Backend</label>
        <div class="field">
          <select id="db_backend" name="db_backend">
            {"".join(f'<option value="{v}" {"selected" if values["db_backend"] == v else ""}>{html.escape(label)}</option>' for v, label in _DB_BACKEND_OPTIONS)}
          </select>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>General</h2>
      <p class="card-help">Voucher Type / Format / default mode, and the two master switches controlling whether automation actually runs.</p>
      {_text_rows(_SETTINGS_FIELDS)}
      {_bool_rows(_SETTINGS_BOOLS)}
    </div>

    <div class="card">
      <h2>Import dialog checkboxes</h2>
      <p class="card-help">Self-healed to these values before every run — another Busy user can change these on screen between pushes (see <code>busy_automation.py::ensure_checkbox_states</code>).</p>
      {_bool_rows(_CHECKBOX_SETTINGS_BOOLS)}
    </div>

    <button type="submit" class="btn">Save</button>
  </form>
</div>
</body>
</html>"""


@app.get("/settings", response_class=HTMLResponse)
def get_settings_page():
    return _settings_page_html()


@app.post("/settings")
async def post_settings_page(request: Request):
    form = await request.form()
    db_backend = form.get("db_backend", settings_store.DEFAULTS["db_backend"])
    if db_backend not in dict(_DB_BACKEND_OPTIONS):
        db_backend = settings_store.DEFAULTS["db_backend"]
    settings_store.set_value("db_backend", db_backend)
    for key, _, _ in _SETTINGS_FIELDS:
        settings_store.set_value(key, form.get(key, settings_store.DEFAULTS[key]))
    all_bool_keys = [k for k, _, _ in _SETTINGS_BOOLS] + [k for k, _, _ in _CHECKBOX_SETTINGS_BOOLS]
    for key in all_bool_keys:
        settings_store.set_value(key, "1" if form.get(key) == "1" else "0")
    return RedirectResponse(url="/settings", status_code=303)


# --- Push UI -----------------------------------------------------------
# A form front-end over the same _push_voucher_core() the JSON API uses --
# lets a human paste/edit a voucher payload and push it without needing
# curl or the Swagger /docs page. Same visual conventions as /settings.

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def _sample_files() -> list[tuple[str, str]]:
    """(filename, contents) for every samples/*.json file, sorted by name."""
    if not SAMPLES_DIR.exists():
        return []
    return [(p.name, p.read_text()) for p in sorted(SAMPLES_DIR.glob("*.json"))]


def _push_page_html(submitted_json: str = "", result_html: str = "") -> str:
    samples = _sample_files()
    default_json = submitted_json or (samples[0][1] if samples else "{}")
    # data-sample (HTML-escaped, read via a delegated click listener below)
    # rather than embedding raw JSON straight into an onclick="..." HTML
    # attribute -- real bug found live: json.dumps() produces a DOUBLE-
    # quoted JS string, which broke the also-double-quoted onclick
    # attribute the moment the JSON contained its own quotes (i.e. always).
    # The buttons rendered but silently did nothing on click.
    sample_buttons = "".join(
        f'<button type="button" class="btn btn-outline btn-sm sample-btn" data-sample="{html.escape(contents)}">{html.escape(name)}</button>'
        for name, contents in samples
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<title>Busy Voucher Push — Push a Voucher</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_PAGE_STYLE}
.btn-outline {{ background: transparent; border-color: var(--border); color: var(--text); }}
.btn-outline:hover {{ border-color: var(--accent); color: var(--accent); background: var(--accent-tint); }}
.btn-sm {{ min-height: 34px; padding: 6px 12px; font-size: 13px; border-radius: var(--radius-sm); }}
.btn-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
textarea {{ width: 100%; box-sizing: border-box; min-height: 320px; padding: 12px; font-family: 'SFMono-Regular', Consolas, monospace; font-size: 13px; border: 1.5px solid var(--border); border-radius: var(--radius-sm); color: var(--text); }}
textarea:focus {{ outline: none; border-color: var(--accent); }}
pre {{ background: var(--surface-muted); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 14px; font-size: 13px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }}
.result-ok {{ background: var(--green-bg); border: 1px solid var(--green-border); color: var(--green-text); border-radius: var(--radius-sm); padding: 12px 14px; font-weight: 600; margin-bottom: 12px; }}
.result-err {{ background: #fee2e2; border: 1px solid #f8b4b4; color: #991b1b; border-radius: var(--radius-sm); padding: 12px 14px; font-weight: 600; margin-bottom: 12px; }}
.loading-overlay {{ display: none; position: fixed; inset: 0; background: rgba(15,23,42,0.55); z-index: 1000; align-items: center; justify-content: center; flex-direction: column; gap: 16px; }}
.loading-overlay.active {{ display: flex; }}
.spinner {{ width: 40px; height: 40px; border: 4px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 0.8s linear infinite; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.loading-text {{ color: #fff; font-size: 15px; font-weight: 600; text-align: center; max-width: 320px; }}
</style>
</head>
<body>
<div class="page">
  <div class="page-header">
    <h1 class="page-title">Push a Voucher</h1>
  </div>
  <p class="page-header-subtitle">Paste or edit a voucher JSON payload and push it through the same pipeline external systems use (POST /vouchers) — preflight-validated, then imported into Busy if Auto-import is on (see <a href="/settings">Settings</a>).</p>

  {result_html}

  <div class="card">
    <h2>Sample payloads</h2>
    <p class="card-help">Click to load into the editor below.</p>
    <div class="btn-row">{sample_buttons or "<span class='card-help'>No samples found in samples/</span>"}</div>

    <form method="post" action="/push" id="push-form">
      <textarea id="voucher_json" name="voucher_json">{html.escape(default_json)}</textarea>
      <div style="margin-top:14px">
        <button type="submit" class="btn" id="push-submit-btn">Push Voucher</button>
      </div>
    </form>
  </div>
</div>

<div class="loading-overlay" id="loading-overlay">
  <div class="spinner"></div>
  <div class="loading-text">Pushing voucher — if Auto-import is on, this drives the real Busy import and can take up to 30 seconds. Please wait…</div>
</div>

<script>
document.querySelectorAll('.sample-btn').forEach(function(btn) {{
  btn.addEventListener('click', function() {{
    document.getElementById('voucher_json').value = btn.dataset.sample;
  }});
}});
document.getElementById('push-form').addEventListener('submit', function() {{
  document.getElementById('loading-overlay').classList.add('active');
  var btn = document.getElementById('push-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Pushing…';
}});
</script>
</body>
</html>"""


# In-process store for the POST-Redirect-GET pattern below -- keyed by a
# random token, holds the (submitted_json, result_html) pair just long
# enough to render once after the redirect. Single-process internal tool,
# not meant to survive a restart or be shared across processes.
_PUSH_RESULTS: dict[str, tuple[str, str]] = {}


@app.get("/push", response_class=HTMLResponse)
def get_push_page(result: str | None = None):
    if result and result in _PUSH_RESULTS:
        submitted_json, result_html = _PUSH_RESULTS.pop(result)
        return _push_page_html(submitted_json=submitted_json, result_html=result_html)
    return _push_page_html()


def _render_push_result(raw: str, result_html: str) -> RedirectResponse:
    """POST-Redirect-GET: real bug found live -- returning the result HTML
    directly from the POST handler meant reloading/refreshing the browser
    re-submitted the exact same POST, which (with Auto-import on) fired a
    SECOND real Busy import for the same voucher on every refresh. Redirect
    to a GET instead so refreshing just re-fetches the same already-
    computed result, never resubmits the form."""
    import secrets

    token = secrets.token_urlsafe(16)
    _PUSH_RESULTS[token] = (raw, result_html)
    return RedirectResponse(url=f"/push?result={token}", status_code=303)


@app.post("/push")
async def post_push_page(request: Request):
    form = await request.form()
    raw = form.get("voucher_json", "")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        result_html = f'<div class="result-err">Invalid JSON: {html.escape(str(e))}</div>'
        return _render_push_result(raw, result_html)

    try:
        voucher = WmsVoucher(**data)
    except ValidationError as e:
        result_html = f'<div class="result-err">Validation failed:</div><pre>{html.escape(str(e))}</pre>'
        return _render_push_result(raw, result_html)

    ok, response = _push_voucher_core(voucher)
    pretty = html.escape(json.dumps(response, indent=2, default=str))
    if ok:
        result_html = f'<div class="result-ok">Status: {html.escape(response.get("status", "?"))}</div><pre>{pretty}</pre>'
    else:
        result_html = f'<div class="result-err">Preflight validation failed</div><pre>{pretty}</pre>'
    return _render_push_result(raw, result_html)
