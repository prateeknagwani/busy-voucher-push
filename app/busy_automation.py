"""Drives Busy's 'Import Vouchers From Excel' dialog via pywinauto (uia backend).

SAFETY DESIGN -- read this before changing anything:

  - Every function that only READS the dialog's current state (inspect_fields,
    find_dialog) is always safe to call -- no clicks, no field writes.
  - Every function that WRITES a field or clicks a button is gated behind
    BUSY_AUTOMATION_ARMED=1 in the environment. Without it, set_* functions
    log what they WOULD do and return without touching the UI, and
    click_import() raises RuntimeError outright rather than silently no-op'ing
    (a click is the one truly irreversible action here -- it must never
    almost-happen by accident).
  - click_import() itself only fires the click. It does not yet know how to
    interpret Busy's post-click result (success toast vs. validation-error
    popup vs. e-Invoice prompt) -- that requires watching a REAL failure and
    a REAL success happen live, which hasn't been done yet (see
    findings/EXCEL_IMPORT_DIALOG.md's Open Questions). Treat run_one_import's
    "result" as PRELIMINARY until validated against at least one real success
    and one real deliberate failure.

PRECONDITION SOLVED (2026-09-18): the user created a Busy keyboard shortcut
(Ctrl+W, via Busy's own 'Create Shortcut' button on the Administration menu)
that opens 'Import Vouchers From Excel' directly -- confirmed live via
open_import_dialog() below. Busy's menu bar itself is NOT automatable (no
real Win32 HMENU, no UIA TreeItem elements -- fully custom-drawn, see
README.md), so this shortcut is what makes fully unattended automation
possible at all; without it, a human would need to open the dialog by hand
every time.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from pywinauto import Desktop

from . import settings_store

REAL_TITLE_SUBSTR = "SGP DISTRIBUTORS"
DIALOG_TITLE = "Import Vouchers From Excel"


def _armed() -> bool:
    """BUSY_AUTOMATION_ARMED=1 in the environment always forces armed
    (dev/CI convenience, e.g. a single supervised test run). Otherwise reads
    the persisted 'automation_armed' setting (app/settings_store.py,
    editable via the /settings GUI) -- so arming is a durable operator
    decision, not something that resets on every process restart."""
    env = os.environ.get("BUSY_AUTOMATION_ARMED")
    if env is not None:
        return env == "1"
    return settings_store.get_bool("automation_armed")


def find_main_window():
    d = Desktop(backend="uia")
    return d.window(title_re=f".*{REAL_TITLE_SUBSTR}.*")


def find_dialog():
    main = find_main_window()
    dlg = main.child_window(title=DIALOG_TITLE, control_type="Window")
    dlg.wait("exists", timeout=5)
    return dlg


def open_import_dialog(timeout: float = 5.0):
    """Opens 'Import Vouchers From Excel' via the Ctrl+W shortcut the user
    created in Busy for this purpose (Administration menu's 'Create
    Shortcut' button). This is what makes unattended automation possible at
    all -- Busy's own menu bar has no real Win32 menu and no UIA TreeItem
    elements to navigate (confirmed, see README.md), so without a shortcut
    like this a human would have to open the dialog by hand every time.

    Idempotent: if the dialog is already open (e.g. left open by a previous
    run, or by a human), reused instead of blindly resending the shortcut.

    Confirmed live (user-verified): Ctrl+W works from ANY Busy screen, not
    just the main dashboard -- e.g. even with an unrelated report window
    (Day Book) open on top and blocking main. The bug was never Ctrl+W
    itself, it was HOW it was sent: main.type_keys() calls pywinauto's
    verify_actionable(), which raises ElementNotEnabled whenever ANY modal
    child is up, even one Ctrl+W would happily cut through if delivered.
    Fixed by sending it as a genuine OS-level keystroke via
    pywinauto.keyboard.send_keys() (goes to whatever has real OS focus,
    same as a human pressing the keys) instead of through main's own
    wrapper -- no enabled-state check to trip over. main.set_focus() is
    still attempted first, best-effort, to maximize the chance Busy has
    real focus when the keystroke fires; its own failure is not fatal here
    (unlike type_keys, set_focus doesn't require the target to be enabled).

    Gated behind BUSY_AUTOMATION_ARMED like every other UI-driving function
    here, even though sending a keyboard shortcut is low-risk/reversible on
    its own -- it still changes what's on screen in a live session someone
    else may be using, so it shouldn't fire silently without being armed."""
    if not _armed():
        print("[DRY RUN] would send Ctrl+W to open the Import Vouchers From Excel dialog")
        return None
    main = find_main_window()
    try:
        existing = main.child_window(title=DIALOG_TITLE, control_type="Window")
        existing.wait("exists", timeout=1)
        return existing
    except Exception:
        pass
    try:
        main.set_focus()
    except Exception:
        pass
    time.sleep(0.3)
    from pywinauto.keyboard import send_keys

    send_keys("^w")
    dlg = main.child_window(title=DIALOG_TITLE, control_type="Window")
    dlg.wait("exists", timeout=timeout)
    return dlg


def reset_busy_to_clean_state(max_iterations: int = 10) -> int:
    """Closes every stray child window under Busy's main window -- leftover
    popups AND the 'Import Vouchers From Excel' dialog itself, if open --
    so every run starts from a guaranteed-clean dashboard state rather than
    trusting whatever a prior run or another Busy user left behind.

    Confirmed necessary live: a success popup left un-dismissed by one run
    silently blocked the NEXT run's dialog entirely (ElementNotEnabled).
    Rather than keep discovering new leftover-state failure modes one at a
    time, this sweeps everything at the start of every run instead.

    Closing the Import dialog itself too (not just popups) is deliberate --
    open_import_dialog() reopens it fresh via Ctrl+W right after, and
    ensure_voucher_type_and_format() self-heals its Voucher Type/Format
    regardless of what a freshly-opened dialog remembers, so a full reset
    is no more expensive than a partial one and rules out other not-yet-
    discovered stale-field issues (Bill Sundries, key field mappings, etc.)
    by construction. Returns the number of windows closed."""
    if not _armed():
        print("[DRY RUN] would close all stray Busy child windows")
        return 0
    main = find_main_window()
    closed = 0
    for _ in range(max_iterations):
        windows = [c for c in main.descendants(control_type="Window") if c.window_text()]
        if not windows:
            break
        for w in windows:
            title = w.window_text()
            if _close_one_window(w, title):
                closed += 1
        time.sleep(0.5)
    return closed


# Buttons commonly used to dismiss a Busy window when a plain WM_CLOSE/
# Escape doesn't work -- tried in this order. Confirmed necessary live: a
# Day Book report window (real accessibility title "Report Options !", not
# an error popup -- just a report a user had open) didn't respond to either
# .close() or Escape at all, unlike every popup seen before it.
_CLOSE_BUTTON_TITLES = ["Quit", "Close", "Cancel", "No"]


def _close_one_window(w, title: str) -> bool:
    """Tries progressively more forceful strategies to dismiss ONE window,
    stopping at the first that actually works (verified by re-checking the
    window no longer exists) rather than just assuming an attempt
    succeeded. Returns True if the window is confirmed gone."""

    def _gone() -> bool:
        try:
            return not w.exists()
        except Exception:
            return True

    try:
        w.close()
        if _gone():
            return True
    except Exception:
        pass

    for btn_title in _CLOSE_BUTTON_TITLES:
        try:
            for b in w.descendants(control_type="Button"):
                if b.window_text() == btn_title:
                    b.click_input()
                    time.sleep(0.3)
                    if _gone():
                        return True
        except Exception:
            pass

    try:
        w.set_focus()
        w.type_keys("{ESC}")
        time.sleep(0.3)
        if _gone():
            return True
    except Exception:
        pass

    # Deliberately NO Alt+F4 fallback: confirmed live that sending it here
    # triggered Busy's own Hot-Key overlay (a real 'Form1' popup listing
    # Ctrl+Alt+<letter> shortcuts, with its own 'Exit (Esc)' button) as an
    # unintended side effect -- the Alt keydown alone is enough to summon
    # it in this app, independent of whether F4 ever registered. That
    # overlay then became a NEW blocker on top of the one this was trying
    # to close, net negative. If close()/button-search/Escape don't work,
    # stop and report rather than trying a more forceful keystroke blind.
    return False


def _file_path_edit(dlg):
    """The Excel File Path box -- identified as the widest Edit control inside
    the 'Excel/Google Sheet File Info' group (confirmed ~601px wide vs. the
    3 narrow Sheet/Row edits at ~61px, see findings/09_excel_import_dialog_tree.txt).
    Positional/width heuristics, not a stable auto_id -- re-verify with
    inspect_fields() if this ever mis-selects after a Busy update."""
    group = dlg.child_window(title="Excel/Google Sheet File Info", control_type="Group")
    edits = group.descendants(control_type="Edit")
    return max(edits, key=lambda e: e.rectangle().width())


def _row_edits_left_to_right(dlg):
    """Sheet No. / Starting Row / Ending Row, in on-screen left-to-right order
    (confirmed via screenshot 10_excel_import_dialog_screenshot.png)."""
    group = dlg.child_window(title="Excel/Google Sheet File Info", control_type="Group")
    edits = group.descendants(control_type="Edit")
    fp = _file_path_edit(dlg)
    narrow = [e for e in edits if e != fp]
    narrow.sort(key=lambda e: e.rectangle().left)
    return narrow  # [sheet_no, starting_row, ending_row]


def _voucher_type_format_edits(dlg):
    """The 'Select Voucher Type' / 'Select Format' value boxes -- plain Edit
    controls near the top of the dialog (L170/L780, T99), readable even when
    their dropdown is closed. Confirmed live: these drift to 'Sales Order'/
    blank after other Busy screens are used in between runs (e.g. checking
    something on 'Add Sales Voucher') -- NOT caused by this code, but this
    code must defend against it every run rather than trust remembered
    dialog state."""
    edits = [e for e in dlg.descendants(control_type="Edit") if e.rectangle().top < 100]
    edits.sort(key=lambda e: e.rectangle().left)
    return edits  # [voucher_type_edit, format_edit]


def ensure_voucher_type_and_format(dlg, voucher_type: str | None = None, format_name: str | None = None) -> None:
    """Self-healing fix for the Voucher-Type/Format drift confirmed live
    (findings/EXCEL_IMPORT_DIALOG.md, README.md) -- checks the current value
    and only touches the dropdown if it's already wrong. Selecting a
    ListItem from an OPEN dropdown is the one part of this dialog that's
    genuinely UIA-accessible (confirmed live); the value Edit boxes
    themselves don't support direct text entry for this field, only
    dropdown selection.

    voucher_type/format_name default to the persisted settings (editable via
    the /settings GUI) rather than a hardcoded literal -- this is exactly
    the "other users might change Busy settings" problem the settings store
    exists to solve: fixing a future drift to a DIFFERENT correct value
    (e.g. a new Format name) is a settings-page edit, not a code change."""
    voucher_type = voucher_type or settings_store.get("voucher_type")
    format_name = format_name or settings_store.get("format_name")
    if not _armed():
        print(f"[DRY RUN] would ensure Voucher Type={voucher_type!r}, Format={format_name!r}")
        return
    vt_edit, fmt_edit = _voucher_type_format_edits(dlg)
    if vt_edit.window_text().strip() != voucher_type:
        vt_edit.click_input()
        time.sleep(0.3)
        _select_list_item(dlg, voucher_type)
        time.sleep(0.5)
        # selecting a new Voucher Type resets Format -- always re-check it
        vt_edit, fmt_edit = _voucher_type_format_edits(dlg)
    if fmt_edit.window_text().strip() != format_name:
        fmt_edit.click_input()
        time.sleep(0.3)
        _select_list_item(dlg, format_name)
        time.sleep(0.5)


def _select_list_item(dlg, item_title: str) -> None:
    item = dlg.child_window(title=item_title, control_type="ListItem")
    item.wait("exists", timeout=5)
    item.click_input()


def ensure_checkbox_states(dlg) -> None:
    """Self-healing fix for every OTHER checkbox on the dialog besides
    Add-New/Modify-Existing (which run_one_import's own `mode` argument
    already controls per-call, not a drift-prone constant) -- Skip Items
    With Zero Quantity, Pick Data from Item Master (Price/Tax Rate/Cess
    Rate/MRP), Auto Calculate Amount (Item/Tax/Cess Amount). Same reasoning
    as ensure_voucher_type_and_format: another Busy user touching a
    different screen between pushes can leave these in an unexpected state,
    so every run checks and corrects rather than trusting what's on screen.
    Known-good defaults (see settings_store.py) were confirmed live against
    a real successful taxed voucher import (2026-09-18, VchCode=7687) --
    correcting a checkbox here toward a DIFFERENT value than what's proven
    to work is a settings-page edit, not something to guess at blindly."""
    if not _armed():
        print("[DRY RUN] would ensure Item Fields / Skip Zero Qty checkbox states match settings")
        return
    for key, title in settings_store.CHECKBOX_TITLES.items():
        desired = settings_store.get_bool(key)
        cb = dlg.child_window(title=title, control_type="CheckBox")
        current = cb.get_toggle_state() == 1
        if current != desired:
            cb.click_input()


def ensure_gst_report_basis(dlg) -> None:
    """GST Report Basis (2-option radio, 'As Per Party Master'/'Billing-
    Shipping Details') -- confirmed GRAYED OUT/disabled in every screenshot
    taken so far, root cause unconfirmed. Skipped harmlessly while disabled
    rather than raising, since forcing a disabled control is neither
    possible nor meaningful; wired in now so it self-heals automatically
    the moment it's found to be genuinely selectable in some voucher/
    format combination."""
    if not _armed():
        print("[DRY RUN] would ensure GST Report Basis matches settings (if enabled)")
        return
    desired = settings_store.get("gst_report_basis")
    radio = dlg.child_window(title=desired, control_type="RadioButton")
    if not radio.is_enabled():
        return
    if radio.get_toggle_state() != 1:
        radio.click_input()


@dataclass
class DialogFields:
    excel_file_path: str
    sheet_no: str
    starting_row: str
    ending_row: str
    add_new_checked: bool
    modify_existing_checked: bool


def inspect_fields(dlg=None) -> DialogFields:
    """Read-only. Safe to call any time the dialog is open."""
    dlg = dlg or find_dialog()
    fp = _file_path_edit(dlg)
    sheet_no, start_row, end_row = _row_edits_left_to_right(dlg)
    add_new = dlg.child_window(title="Add New Vouchers", control_type="CheckBox")
    modify = dlg.child_window(title="Modify Existing Vouchers", control_type="CheckBox")
    return DialogFields(
        excel_file_path=fp.get_value() if hasattr(fp, "get_value") else fp.window_text(),
        sheet_no=sheet_no.window_text(),
        starting_row=start_row.window_text(),
        ending_row=end_row.window_text(),
        add_new_checked=add_new.get_toggle_state() == 1,
        modify_existing_checked=modify.get_toggle_state() == 1,
    )


def set_excel_file_path(dlg, path: str | Path) -> None:
    path = str(Path(path).resolve())
    if not _armed():
        print(f"[DRY RUN] would set Excel File Path to: {path!r}")
        return
    fp = _file_path_edit(dlg)
    fp.set_focus()
    fp.set_edit_text(path)


def set_mode(dlg, mode: str) -> None:
    """mode: 'ADD' or 'MODIFY'. Toggles the two checkboxes to match -- Busy's
    own UI has them as independent checkboxes, not a radio pair (confirmed
    from the tree dump), so both must be driven explicitly."""
    if mode not in ("ADD", "MODIFY"):
        raise ValueError(f"mode must be ADD or MODIFY, got {mode!r}")
    if not _armed():
        print(f"[DRY RUN] would set mode to: {mode}")
        return
    add_new = dlg.child_window(title="Add New Vouchers", control_type="CheckBox")
    modify = dlg.child_window(title="Modify Existing Vouchers", control_type="CheckBox")
    want_add = mode == "ADD"
    if (add_new.get_toggle_state() == 1) != want_add:
        add_new.click_input()
    if (modify.get_toggle_state() == 1) != (not want_add):
        modify.click_input()


def click_import(dlg) -> None:
    """The one genuinely irreversible action in this module. Hard-refuses
    unless BUSY_AUTOMATION_ARMED=1 -- no silent dry-run fallback here, unlike
    the set_* functions, because a caller accidentally treating a no-op as a
    real import would be worse than a loud failure."""
    if not _armed():
        raise RuntimeError(
            "click_import() refused: set BUSY_AUTOMATION_ARMED=1 to allow this. "
            "This clicks Import in the LIVE production Busy session."
        )
    btn = dlg.child_window(title="Import", control_type="Button")
    btn.wait("enabled", timeout=5)
    btn.click_input()


def _new_popup(main, known_titles: set[str]):
    for c in main.descendants(control_type="Window"):
        t = c.window_text()
        if t and t not in known_titles and t != DIALOG_TITLE:
            return c
    return None


def _click_button_in(popup, title: str) -> None:
    """popup comes from descendants() -- a plain UIAWrapper, which has no
    child_window() (that only exists on WindowSpecification objects from
    app.window()/child_window() chains). Real bug hit live: AttributeError
    on the first real run with this popup-detection path. Walk descendants
    directly instead."""
    for b in popup.descendants(control_type="Button"):
        if b.window_text() == title:
            b.click_input()
            return
    raise LookupError(f"no {title!r} button found in popup {popup.window_text()!r}")


def _popup_message_text(popup) -> str:
    try:
        texts = [t.window_text() for t in popup.descendants(control_type="Text")]
        return " ".join(t for t in texts if t)
    except Exception:
        return ""


def _popup_button_titles(popup) -> set[str]:
    try:
        return {b.window_text() for b in popup.descendants(control_type="Button") if b.window_text()}
    except Exception:
        return set()


# Popups confirmed real and understood -- (title substring, REQUIRED exact
# button set, button to click). Only exactly these are auto-answered;
# anything else (including a DIFFERENT popup that happens to share a
# title, see below) bails to NEEDS_HUMAN_ATTENTION.
#
# Real bug found live (2026-09-18): matching on title alone was wrong --
# "Invalid Data !" is Busy's GENERIC error-dialog title, reused for
# completely different problems. The numbering-mode warning (Yes/No, safe
# to click Yes for the verified-Manual ZManual series) and "Excel file does
# not exist." (OK only, a genuine fatal error that must never be
# auto-dismissed as if it were fine) both use this exact same title. A
# stray test run with a bad file path hit exactly this and crashed with
# LookupError (no 'Yes' button in a popup that only had 'OK') -- harmless
# in that case, but auto-clicking 'Yes' on a real "file does not exist"
# popup would have been a much worse silent failure if a 'Yes' button had
# existed.
#
# Tried gating on the popup's message text first -- confirmed live that
# doesn't work: this popup's text ("Vouchers can not be imported if
# voucher numbering is Automatic or Not Required...") is rendered
# owner-drawn, not exposed as any UIA Text element at all (confirmed via a
# live descendants() dump -- only Pane/Group/Button, no Text controls).
# Matching on the BUTTON SET instead is reliable: confirmed live these two
# real "Invalid Data !" popups have genuinely different button sets
# ({Yes,No} vs {OK}), which IS exposed via UIA regardless of the message
# text situation.
KNOWN_SAFE_POPUPS = [
    ("Start Data Import", {"Yes", "No"}, "Yes"),  # standard pre-import confirmation, always expected
    ("Invalid Data", {"Yes", "No"}, "Yes"),  # the numbering-mode warning specifically (Yes/No only) -- unconfirmed root cause, see README.md
]

# The actual terminal success signal (confirmed live, 2026-09-18: "'1'
# Voucher imported successfully."). Dismissed with OK, but reported as a
# real SUCCESS status, not folded into KNOWN_SAFE_POPUPS's "click and keep
# watching" loop -- this is the end of the chain, not another gate. A prior
# run's success popup left un-dismissed silently blocked the whole dialog
# for the NEXT run (confirmed live -- caused a real ElementNotEnabled
# failure) -- always let this run to completion, never leave it hanging.
SUCCESS_POPUP_TITLE = "Vouchers Import"


def wait_for_outcome(dlg, timeout: float = 15.0, auto_confirm_known: bool = True, known_titles: set[str] | None = None) -> dict:
    """Polls the MAIN WINDOW'S DESCENDANTS (not just direct children, and NOT
    Desktop()'s top-level windows -- Busy's popups nest as UIA children of
    the main window, sometimes nested under the dialog itself) for any new
    popup. If auto_confirm_known and the popup's title matches
    KNOWN_SAFE_POPUPS, clicks the mapped button and keeps watching (a real
    import can chain more than one gate -- confirmed live: Start Data Import
    then Invalid Data, back to back, before the real success). Any
    unrecognized popup stops the loop and returns NEEDS_HUMAN_ATTENTION
    rather than guessing -- per the 'detect and bail rather than hang'
    requirement. auto_confirm_known=False reproduces the original
    detect-only behavior.

    known_titles: pass the pre-click snapshot explicitly (see
    run_one_import) -- taking it INSIDE this function, after the caller
    already clicked Import, is a real bug that was hit live: a popup that
    appears synchronously/instantly gets treated as "already there" since
    the baseline snapshot is taken too late to have missed it. If omitted,
    falls back to snapshotting now (only safe if called before the click)."""
    main = find_main_window()
    if known_titles is None:
        known_titles = {c.window_text() for c in main.descendants(control_type="Window")}
    confirmed_chain = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        popup = _new_popup(main, known_titles)
        if popup is not None:
            title = popup.window_text()
            if SUCCESS_POPUP_TITLE in title:
                message = _popup_message_text(popup)
                _click_button_in(popup, "OK")
                return {
                    "status": "SUCCESS",
                    "message": message,
                    "auto_confirmed_before_success": confirmed_chain,
                }
            buttons = _popup_button_titles(popup)
            match = next(
                (
                    (title_sub, button)
                    for title_sub, required_buttons, button in KNOWN_SAFE_POPUPS
                    if title_sub in title and buttons == required_buttons
                ),
                None,
            )
            if auto_confirm_known and match:
                _, button = match
                _click_button_in(popup, button)
                confirmed_chain.append(title)
                known_titles.add(title)
                time.sleep(0.5)
                continue
            return {
                "status": "NEEDS_HUMAN_ATTENTION",
                "new_window_title": title,
                "new_window_message": _popup_message_text(popup),  # often empty -- owner-drawn text isn't always UIA-readable, see KNOWN_SAFE_POPUPS docstring
                "new_window_buttons": sorted(buttons),
                "new_window_class": popup.element_info.class_name,
                "auto_confirmed_before_this": confirmed_chain,
            }
        time.sleep(0.5)
    return {
        "status": "NO_NEW_WINDOW_DETECTED_WITHIN_TIMEOUT",
        "auto_confirmed": confirmed_chain,
    }


def run_one_import(xlsx_path: str | Path, mode: str | None = None, auto_confirm_known: bool = True) -> dict:
    """Full pipeline: reset to a clean state, open the dialog fresh (Ctrl+W),
    self-heal Voucher Type/Format/every checkbox/GST Report Basis, point it
    at xlsx_path, set the mode, click Import, auto-confirm the known gates
    (see KNOWN_SAFE_POPUPS), report the outcome. Validated live end-to-end
    (auto_confirm_known=True) -- 2026-09-18, VchCode=7684,
    ZManual/26-27/00013, confirmed via direct DB read.

    mode: "ADD" or "MODIFY", defaults to the persisted 'default_mode'
    setting (see settings_store.py) if not given."""
    mode = mode or settings_store.get("default_mode")
    reset_busy_to_clean_state()
    dlg = open_import_dialog()
    if dlg is None:  # dry run
        ensure_voucher_type_and_format(None)
        ensure_checkbox_states(None)
        ensure_gst_report_basis(None)
        set_excel_file_path(None, xlsx_path)
        set_mode(None, mode)
        print("[DRY RUN] would click Import")
        return {"status": "DRY_RUN"}

    ensure_voucher_type_and_format(dlg)
    ensure_checkbox_states(dlg)
    ensure_gst_report_basis(dlg)
    set_excel_file_path(dlg, xlsx_path)
    set_mode(dlg, mode)
    main = find_main_window()
    known_titles = {c.window_text() for c in main.descendants(control_type="Window")}
    click_import(dlg)
    return wait_for_outcome(dlg, auto_confirm_known=auto_confirm_known, known_titles=known_titles)
