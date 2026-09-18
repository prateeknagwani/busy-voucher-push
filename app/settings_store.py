"""Persisted settings for the known-good Busy dialog configuration.

Why this exists: Voucher Type / Format drift away from "Sales"/"Sales Import"
whenever another Busy user touches a different screen in between pushes --
confirmed live, twice, not a hypothetical (see README.md). Hardcoding the
correct values in busy_automation.py meant every drift needed a code change
to fix. This makes them editable data instead, via the settings GUI
(app/main.py's /settings page), so a config change here never needs a
redeploy.

sqlite-backed (same pattern as app/idempotency.py) -- survives restarts,
single source of truth for both the API server and any script importing
this module directly.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULTS = {
    # VESTIGIAL as of 2026-09-18 -- nothing in app/main.py branches on this
    # anymore. It USED to be the switch deciding whether preflight
    # validation (app/sql_preflight.py) ran at all; that whole check was
    # removed (masters-existence, same day as the duplicate-number check
    # before it -- "let Busy handle it", now fully consistent across every
    # backend). Left in place (not deleted) as a documented record of which
    # real database this Busy install uses, since app/sql_preflight.py's
    # connect_readonly() and config.json's backend-specific connection
    # strings still exist and still work if something reintroduces a need
    # for a live-DB check later -- but nothing reads this setting today.
    "db_backend": "sqlserver",
    "voucher_type": "Sales",
    "format_name": "Sales Import",
    "automation_armed": "0",
    "auto_import_on_push": "0",  # if "1", POST /vouchers also runs the real import, not just generates the file
    # Every other checkbox on the Import dialog that's been observed to
    # drift when another Busy user touches a different screen in between
    # pushes (same root cause as Voucher Type/Format drift -- see
    # busy_automation.py::ensure_checkbox_state). Values below are the
    # known-good state confirmed live against a real successful taxed
    # voucher import (2026-09-18, VchCode=7687).
    "default_mode": "ADD",  # "ADD" or "MODIFY" -- see busy_automation.py::set_mode. Unlike the checkboxes
    # below, Add New Vouchers / Modify Existing Vouchers can't actually drift silently: set_mode()
    # explicitly sets BOTH checkboxes to match `mode` every single run (deterministic set, not a
    # check-then-maybe-click pattern), so there's nothing to self-heal there. This setting only
    # controls what `mode` defaults to when a caller doesn't specify one.
    "skip_items_zero_qty": "0",
    "create_new_master": "0",
    "pick_price": "0",
    "pick_tax_rate": "1",
    "pick_cess_rate": "0",
    "pick_mrp": "0",
    "auto_item_amount": "0",
    "auto_tax_amount": "1",
    "auto_cess_amount": "0",
    # GST Report Basis (radio, under 'GST Report Basis' group) -- confirmed
    # GRAYED OUT/disabled in every screenshot taken so far (root cause
    # unconfirmed -- possibly needs GSTInfo=true on the voucher type/format
    # to become live). Setting exists so it's ready the moment it becomes
    # selectable; ensure_checkbox_states skips it harmlessly while disabled.
    "gst_report_basis": "As Per Party Master",  # or "Billing/Shipping Details"
}

# (settings key, exact Busy checkbox title) -- used by
# busy_automation.py::ensure_checkbox_state to find each control.
CHECKBOX_TITLES = {
    "skip_items_zero_qty": "Skip Items With Zero Quantity",
    "create_new_master": "Create New Master Used in Vouchers",
    "pick_price": "Price",
    "pick_tax_rate": "Tax Rate",
    "pick_cess_rate": "Cess Rate",
    "pick_mrp": "MRP",
    "auto_item_amount": "Item Amount",
    "auto_tax_amount": "Tax Amount",
    "auto_cess_amount": "Cess Amount",
}

# GST Report Basis is a 2-option radio group, not a checkbox -- handled
# separately in busy_automation.py::ensure_gst_report_basis.
GST_REPORT_BASIS_OPTIONS = ["As Per Party Master", "Billing/Shipping Details"]


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level="DEFERRED")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get(key: str) -> str:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else DEFAULTS[key]


def get_all() -> dict[str, str]:
    with _conn() as conn:
        rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    return {k: rows.get(k, v) for k, v in DEFAULTS.items()}


def set_value(key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_bool(key: str) -> bool:
    return get(key) == "1"
