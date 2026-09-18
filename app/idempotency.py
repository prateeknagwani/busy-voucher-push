"""Local WMS-side idempotency store for the Busy voucher-push series.

UNUSED as of 2026-09-18 -- kept in the repo (never deleted, per project
policy) but no longer called by app/main.py or app/excel_writer.py.
external_ref and auto-allocation were removed from WmsVoucher per explicit
request ("remove ext ref" / vch_no now REQUIRED, always caller-supplied,
always used exactly as given -- see voucher_model.py). This module's
get_or_allocate()/lookup() and the _highest_existing_no() self-healing
counter check are dead code until/unless something reintroduces a need for
server-side voucher numbering.

Busy has NO stored counter for a Voucher Series (confirmed in
findings/SCHEMA_DISCOVERY.md -- Master1 MasterType=21 rows carry an all-zero
counter; Busy derives the next number live from MAX(AutoVchNo) at save time,
which is unique to no database constraint). Since this utility is meant to be
the ONLY writer into the WMS-only series, the safe design is to own the
counter here instead of asking Busy for it -- one sqlite file, one
transaction per allocation, never a read-then-insert race against Busy's own
concurrent UI use.

external_ref -> (series, vch_no) is permanent once allocated: re-pushing the
same external_ref returns the SAME vch_no rather than allocating a new one,
so a retried WMS push naturally becomes a Busy 'Modify Existing Vouchers'
scenario (matched by Vch.No+Series) rather than a duplicate.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "idempotency.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voucher_map (
    external_ref TEXT PRIMARY KEY,
    series TEXT NOT NULL,
    vch_no INTEGER NOT NULL,
    vch_no_display TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(series, vch_no)
);

CREATE TABLE IF NOT EXISTS series_counter (
    series TEXT PRIMARY KEY,
    next_no INTEGER NOT NULL
);
"""


def _highest_existing_no(series: str, fy_label: str) -> int:
    """Queries the REAL Busy database for the highest voucher number already
    used in this series+FY, so a fresh/wiped local counter never blindly
    restarts from 1 and collides with real history. Confirmed necessary
    live: this local sqlite file was accidentally deleted mid-development
    and the counter really did restart from 1, colliding with a real
    already-used voucher number (caught by sql_preflight's duplicate check,
    so nothing bad was written -- but every push failed until manually
    reseeded). This makes reseeding automatic instead of a manual step
    someone has to remember. Returns 0 (i.e. "start from 1") if no matching
    series/vouchers are found or the DB isn't reachable right now -- fails
    safe to the old behavior rather than blocking allocation entirely.

    Only ever attempted when settings_store's db_backend is 'sqlserver' --
    for 'access'/'none' we have no verified schema to query against, so
    this doesn't even try (not just "fails gracefully"; it never connects
    at all). See settings_store.py's db_backend docstring."""
    from . import settings_store

    if settings_store.get("db_backend") != "sqlserver":
        return 0
    try:
        from .sql_preflight import connect_readonly

        conn = connect_readonly()
    except Exception:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT Code FROM Master1 WHERE MasterType=21 AND Name LIKE ?", (f"%{series}",))
        rows = cur.fetchall()
        if len(rows) != 1:
            return 0  # no match or ambiguous -- same "can't resolve" case sql_preflight treats as an error
        series_code = rows[0][0]
        cur.execute(
            "SELECT VchNo FROM Tran1 WHERE VchSeriesCode=? AND VchNo LIKE ?",
            (series_code, f"%{series}/{fy_label}/%"),
        )
        highest = 0
        for (vch_no,) in cur.fetchall():
            tail = vch_no.strip().rsplit("/", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return highest
    finally:
        conn.close()


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level="DEFERRED")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_or_allocate(external_ref: str, series: str, fy_label: str, manual_vch_no: str | None = None) -> str:
    """Returns the display voucher number, e.g. 'WMS/25-26/00001'.

    Idempotent: the same external_ref always returns the same number, even
    across process restarts (sqlite-backed, not in-memory) -- regardless of
    whether that number was auto-allocated or given via manual_vch_no.

    manual_vch_no: if given (and this external_ref has no mapping yet), used
    EXACTLY as provided instead of auto-incrementing the series counter --
    no local or remote duplicate check at all (removed 2026-09-18, per
    explicit request: "let Busy handle it", same reasoning as
    sql_preflight.py dropping its own Tran1 duplicate check). Two different
    external_refs CAN end up mapped to the identical display string here;
    nothing in this module will catch or prevent that -- Busy's own
    numbering-mode enforcement is the only thing that will, at Import time.
    A manual allocation does NOT advance the series counter, so it can
    never silently cause a later auto-allocated number to collide with it
    (kept in its own negative-int range purely to satisfy the
    UNIQUE(series, vch_no) constraint -- that integer has no meaning here).
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT vch_no_display FROM voucher_map WHERE external_ref=?",
            (external_ref,),
        ).fetchone()
        if row:
            return row[0]

        if manual_vch_no:
            min_row = conn.execute(
                "SELECT MIN(vch_no) FROM voucher_map WHERE series=? AND vch_no<0", (series,)
            ).fetchone()
            synthetic_no = (min_row[0] - 1) if min_row[0] is not None else -1
            conn.execute(
                "INSERT INTO voucher_map(external_ref, series, vch_no, vch_no_display) "
                "VALUES (?, ?, ?, ?)",
                (external_ref, series, synthetic_no, manual_vch_no),
            )
            return manual_vch_no

        cur = conn.execute(
            "SELECT next_no FROM series_counter WHERE series=?", (series,)
        ).fetchone()
        if cur:
            next_no = cur[0]
        else:
            # Cold start for this series -- check real Busy history first
            # rather than assuming 1 (see _highest_existing_no docstring).
            next_no = _highest_existing_no(series, fy_label) + 1

        conn.execute(
            "INSERT INTO series_counter(series, next_no) VALUES (?, ?) "
            "ON CONFLICT(series) DO UPDATE SET next_no=excluded.next_no",
            (series, next_no + 1),
        )
        display = f"{series}/{fy_label}/{next_no:05d}"
        conn.execute(
            "INSERT INTO voucher_map(external_ref, series, vch_no, vch_no_display) "
            "VALUES (?, ?, ?, ?)",
            (external_ref, series, next_no, display),
        )
        return display


def lookup(external_ref: str) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT vch_no_display FROM voucher_map WHERE external_ref=?",
            (external_ref,),
        ).fetchone()
        return row[0] if row else None
