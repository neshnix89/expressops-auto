"""Open Rewrite Desk as a small always-on-top window.

Run once in the morning and leave it on. It opens the published Rewrite Desk
page in Edge's app mode (no tabs, no address bar), parks it bottom-right, and
keeps it above other windows so it stays one click away.

Nothing is installed: this uses the Edge already on the laptop, the Python
already on the laptop, and only the standard library.

Usage (Windows):
    pythonw tools\rewriter\launch_rewriter.pyw            # normal
    pythonw tools\rewriter\launch_rewriter.pyw --no-top   # do not pin on top
    set REWRITER_URL=https://...                          # override the page URL

Sign in to claude.ai in Edge once. The page asks for permission to use your
Claude account the first time you press Rewrite.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

DEFAULT_URL = "https://claude.ai/code/artifact/20e29c97-1c36-47c0-bda6-fb3a3bda7710"
WINDOW_TITLE_PART = "Rewrite Desk"
WIDTH, HEIGHT = 460, 780
MARGIN = 12

BROWSER_CANDIDATES = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    """Return the first Edge or Chrome executable found, else None."""
    for raw in BROWSER_CANDIDATES:
        path = os.path.expandvars(raw)
        if os.path.isfile(path):
            return path
    return None


def work_area() -> tuple[int, int, int, int]:
    """Screen area excluding the taskbar, as (left, top, right, bottom)."""
    rect = wintypes.RECT()
    spi_getworkarea = 0x0030
    if ctypes.windll.user32.SystemParametersInfoW(spi_getworkarea, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    user32 = ctypes.windll.user32
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def launch(browser: str, url: str) -> None:
    """Open the page in app mode, positioned bottom-right of the work area."""
    _, _, right, bottom = work_area()
    x = max(0, right - WIDTH - MARGIN)
    y = max(0, bottom - HEIGHT - MARGIN)
    subprocess.Popen(
        [
            browser,
            f"--app={url}",
            f"--window-size={WIDTH},{HEIGHT}",
            f"--window-position={x},{y}",
        ],
        close_fds=True,
    )


def find_window() -> int | None:
    """Find the visible top-level window whose title names the page."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if WINDOW_TITLE_PART in buf.value:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(on_window, 0)
    return found[0] if found else None


def pin_on_top(hwnd: int) -> None:
    """Set the window topmost without moving, resizing or focusing it."""
    hwnd_topmost = -1
    swp_nosize, swp_nomove, swp_noactivate = 0x0001, 0x0002, 0x0010
    ctypes.windll.user32.SetWindowPos(
        hwnd, hwnd_topmost, 0, 0, 0, 0, swp_nosize | swp_nomove | swp_noactivate
    )


def keep_on_top() -> None:
    """Re-apply topmost every few seconds until the window has been closed."""
    seen = False
    missing_since: float | None = None
    started = time.time()
    while True:
        hwnd = find_window()
        if hwnd:
            seen = True
            missing_since = None
            pin_on_top(hwnd)
        else:
            if not seen and time.time() - started > 60:
                return
            if seen:
                missing_since = missing_since or time.time()
                if time.time() - missing_since > 20:
                    return
        time.sleep(2)


def main(argv: list[str]) -> int:
    if os.name != "nt":
        sys.stderr.write("This launcher is for the Windows laptop. On other machines open the URL in a browser.\n")
        return 1
    url = os.environ.get("REWRITER_URL") or next((a for a in argv if a.startswith("http")), DEFAULT_URL)
    browser = find_browser()
    if not browser:
        ctypes.windll.user32.MessageBoxW(0, "Could not find Edge or Chrome on this laptop.", "Rewrite Desk", 0x10)
        return 1
    launch(browser, url)
    if "--no-top" not in argv:
        keep_on_top()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
