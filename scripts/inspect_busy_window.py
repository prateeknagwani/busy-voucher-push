"""Read-only UI inspection of the running Busy21 window (no clicks, no input).
Prints the control tree so we can plan pywinauto automation of the Data Import
flow without guessing blind. Busy21 is a VB6 (ThunderRT6) app, so both the
'uia' and 'win32' pywinauto backends are tried -- VB6 apps often expose a much
richer tree via the legacy win32/MSAA backend than via UIA."""
from pywinauto import Desktop
import sys

REAL_TITLE_SUBSTR = "EXAMPLE DISTRIBUTORS"

def try_backend(backend):
    print(f"\n{'='*20} backend={backend} {'='*20}")
    d = Desktop(backend=backend)
    try:
        win = d.window(title_re=f".*{REAL_TITLE_SUBSTR}.*")
        win.set_focus()
        print(f"Connected via {backend}. class={win.element_info.class_name!r}")
        win.print_control_identifiers(depth=4)
    except Exception as e:
        print(f"FAILED via {backend}: {type(e).__name__}: {e}")

if __name__ == "__main__":
    for b in ("win32", "uia"):
        try_backend(b)
