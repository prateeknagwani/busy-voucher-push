"""Read-only preflight validation for the direct-SQL voucher writer.

UNUSED as of 2026-09-18 -- kept in the repo (never deleted, per project
policy) but no longer called by app/main.py. The masters-existence check
this module provides was removed entirely (same day/reasoning as the
duplicate-number check before it -- "let Busy handle it", now fully
consistent regardless of db_backend; see app/settings_store.py's
db_backend docstring and app/main.py's module docstring). connect_readonly()
and preflight_check() are dead code until/unless something reintroduces a
need for live-DB validation.

Design goal (per explicit request): wrong data must NEVER reach an INSERT.
Every master this voucher references (party, sale type, material centre,
every item, the voucher series itself) must resolve to a real, existing
Master1 row before any write is attempted. If ANY of that fails, the whole
voucher is rejected with a precise reason; nothing partial is ever queued
for insert.

Deliberately does NOT check for a duplicate voucher number against Tran1
(removed 2026-09-18, per explicit request: "let Busy handle it"). Busy's
own numbering-mode enforcement (the ZManual series is configured with
Duplicate Voucher Number = Don't Allow) is the sole duplicate check now --
same as it always was for anyone typing a voucher in by hand. This does
mean a genuine duplicate number is no longer caught before Busy's own
Import click; what Busy's Excel-import path actually does when handed one
(clean rejection vs. some other behavior) has not been independently
verified here -- not needed, since Busy is now the authority on this, not
this module.

Uses ONLY the existing read-only busy_sync_reader login -- this module never
needs write access and never will.

Confirmed matching rules (verified live against real Tran1/Tran2/Master1
rows for our own test voucher ZManual/26-27/00001, VchCode 7671 -- see
findings/SQL_PREFLIGHT_NOTES.md):
  - Party      -> Master1.Name, MasterType=2  (exact match)
  - Item       -> Master1.Name, MasterType=6  (exact match, no fuzzy fallback)
  - Sale Type  -> Master1.Name, MasterType=13 (exact match)
  - MC Name    -> Master1.Name, MasterType=11 (exact match)
  - Series     -> Master1.Name, MasterType=21 -- Busy auto-prefixes series
    names with a 2-digit sort code (confirmed: our 'ZManual' series is
    actually stored as Name='09ZManual'). Matched here via "ends with the
    given name, case-insensitive" rather than exact-equality, since the
    prefix isn't something a caller can know in advance. If more than one
    series name ends with the given suffix, this is treated as ambiguous
    and rejected rather than guessing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pyodbc

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
CONFIG_LOCAL_PATH = Path(__file__).resolve().parent.parent / "config.local.json"


def _load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    if CONFIG_LOCAL_PATH.exists():
        local = json.loads(CONFIG_LOCAL_PATH.read_text())
        cfg["sqlserver"].update(local.get("sqlserver", {}))
        if "access" in local:
            cfg.setdefault("access", {}).update(local["access"])
        if "backend" in local:
            cfg["backend"] = local["backend"]
    return cfg


def connect_readonly() -> pyodbc.Connection:
    """Backend-agnostic: SQL Server (this install, fully tested/verified
    live against real data -- see findings/SQL_PREFLIGHT_NOTES.md) or MS
    Access (config.json's `backend: "access"` -- added because a different
    Busy install this tool needs to support uses Access, not SQL Server, as
    its backend. UNTESTED against a real Access-backed Busy database -- the
    SQL queries in this module and idempotency.py are assumed to work
    unchanged (same Busy schema either way), but Access's SQL dialect via
    ODBC has real differences from T-SQL (LIKE wildcard/ESCAPE handling in
    particular) that haven't been verified. Treat the first real run against
    an Access backend as a supervised test, the same way the SQL Server path
    was validated live before being trusted)."""
    cfg = _load_config()
    backend = cfg.get("backend", "sqlserver")
    if backend == "access":
        file_path = cfg["access"]["file_path"]
        if not file_path:
            raise RuntimeError(
                "backend is 'access' but config.json/config.local.json's access.file_path is empty -- "
                "set it to the real Busy .mdb/.accdb file path."
            )
        conn_str = (
            "DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={file_path};"
        )
        return pyodbc.connect(conn_str)
    elif backend == "sqlserver":
        sql_cfg = cfg["sqlserver"]
        conn_str = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            f"SERVER={sql_cfg['server']};DATABASE={sql_cfg['database']};"
            f"UID={sql_cfg['readonly_user']};PWD={sql_cfg['readonly_password']};"
        )
        return pyodbc.connect(conn_str)
    else:
        raise ValueError(f"unknown backend {backend!r} in config -- must be 'sqlserver' or 'access'")


def _escape_like(s: str) -> str:
    return s.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")


@dataclass
class ResolvedRefs:
    series_code: int
    series_full_name: str
    party_code: int
    sale_type_code: int
    mc_code: int
    item_codes: dict[str, int] = field(default_factory=dict)


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    resolved: ResolvedRefs | None = None


def _resolve_exact(conn, master_type: int, name: str, label: str) -> tuple[int | None, str | None]:
    cur = conn.cursor()
    cur.execute(
        "SELECT Code FROM Master1 WHERE MasterType=? AND Name=?", (master_type, name)
    )
    rows = cur.fetchall()
    if not rows:
        return None, f"{label} not found in Busy masters: {name!r} (MasterType={master_type})"
    if len(rows) > 1:
        return None, f"{label} name {name!r} is ambiguous -- {len(rows)} masters share this exact name"
    return rows[0][0], None


def _resolve_series(conn, series_name: str) -> tuple[int | None, str | None, str | None]:
    """Returns (code, full_name, error). Matches Busy's auto-prefixed series
    names (e.g. input 'ZManual' matches stored Name '09ZManual') via
    ends-with, case-insensitive."""
    cur = conn.cursor()
    like_pattern = f"%{_escape_like(series_name)}"
    cur.execute(
        "SELECT Code, Name FROM Master1 WHERE MasterType=21 AND Name LIKE ? ESCAPE '['",
        (like_pattern,),
    )
    rows = cur.fetchall()
    if not rows:
        return None, None, f"Voucher series not found in Busy: {series_name!r}"
    if len(rows) > 1:
        names = ", ".join(r[1] for r in rows)
        return None, None, f"Voucher series {series_name!r} is ambiguous -- matches: {names}"
    return rows[0][0], rows[0][1], None


def preflight_check(voucher, candidate_vch_no: str, conn: pyodbc.Connection | None = None) -> PreflightResult:
    """voucher: an app.voucher_model.WmsVoucher.
    candidate_vch_no: the display voucher number this push would use (from
    app.idempotency.get_or_allocate) -- accepted as a parameter for the
    caller's own logging/response purposes, NOT checked against Tran1 for
    duplicates here (see module docstring -- Busy's own numbering-mode
    enforcement is the duplicate check now).
    """
    own_conn = conn is None
    conn = conn or connect_readonly()
    errors: list[str] = []
    try:
        series_code, series_full_name, err = _resolve_series(conn, voucher.series)
        if err:
            errors.append(err)

        party_code, err = _resolve_exact(conn, 2, voucher.party_name, "Party")
        if err:
            errors.append(err)

        sale_type_code, err = _resolve_exact(conn, 13, voucher.sale_type, "Sale Type")
        if err:
            errors.append(err)

        mc_code, err = _resolve_exact(conn, 11, voucher.mc_name, "Material Centre")
        if err:
            errors.append(err)

        item_codes: dict[str, int] = {}
        for item in voucher.items:
            if item.item_name in item_codes:
                continue
            code, err = _resolve_exact(conn, 6, item.item_name, "Item")
            if err:
                errors.append(err)
            else:
                item_codes[item.item_name] = code

        if errors:
            return PreflightResult(ok=False, errors=errors)

        return PreflightResult(
            ok=True,
            resolved=ResolvedRefs(
                series_code=series_code,
                series_full_name=series_full_name,
                party_code=party_code,
                sale_type_code=sale_type_code,
                mc_code=mc_code,
                item_codes=item_codes,
            ),
        )
    finally:
        if own_conn:
            conn.close()
