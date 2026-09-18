"""Interactive-but-safe exploration of Busy's 'Import Vouchers From Excel' dialog.
Only clicks buttons that are read-only/non-destructive in intent:
  - Download Sample File  (writes a template file to disk, no data import)
  - Configure             (opens a field-mapping screen; we inspect then Cancel)
Never touches the actual 'Import' button. Run steps individually via CLI arg
so each action can be reviewed before the next.
"""
import sys
import time
from pywinauto import Desktop

REAL_TITLE_SUBSTR = "EXAMPLE DISTRIBUTORS"
DIALOG_TITLE = "Import Vouchers From Excel"


def get_dialog():
    d = Desktop(backend="uia")
    main = d.window(title_re=f".*{REAL_TITLE_SUBSTR}.*")
    dlg = main.child_window(title=DIALOG_TITLE, control_type="Window")
    dlg.wait("exists", timeout=5)
    return dlg


def download_sample():
    dlg = get_dialog()
    btn = dlg.child_window(title="Download Sample File", control_type="Button")
    btn.wait("enabled", timeout=5)
    print("Clicking 'Download Sample File'...")
    btn.click_input()
    time.sleep(2)
    # A Save-As or a "file ready" dialog likely appears -- inspect the whole
    # desktop's top windows afterward rather than assuming a specific title.
    d = Desktop(backend="uia")
    print("Top-level windows after click:")
    for w in d.windows():
        try:
            t = w.window_text()
        except Exception:
            continue
        if t:
            print(f"  {w.element_info.class_name!r} | {t!r}")


def inspect_configure():
    dlg = get_dialog()
    btn = dlg.child_window(title="Configure", control_type="Button")
    btn.wait("enabled", timeout=5)
    print("Clicking 'Configure'...")
    btn.click_input()
    time.sleep(2)
    d = Desktop(backend="uia")
    print("Top-level windows after Configure click:")
    for w in d.windows():
        try:
            t = w.window_text()
        except Exception:
            continue
        if t:
            print(f"  {w.element_info.class_name!r} | {t!r}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "tree"
    if action == "sample":
        download_sample()
    elif action == "configure":
        inspect_configure()
    else:
        dlg = get_dialog()
        dlg.print_control_identifiers(depth=6)
