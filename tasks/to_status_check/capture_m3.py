"""
capture_m3.py — Capture XDRX800 XML responses for mock testing.

Run on company laptop to collect real TO data into mock_data/ files.
These files let the M3H5Client work in mock mode on the VPS.

Usage:
    python tasks/to_status_check/capture_m3.py

Launches Edge, authenticates, opens XDRX800, searches for your TOs,
and saves the XML response as mock_data/to_all.xml.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
MOCK_DIR = TASK_DIR / "mock_data"
M3_PORTAL = "https://pfde-m3-auth.eu.p-f.biz/mne/"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright")
        print("Then: python -m playwright install chromium")
        return 1

    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []

    def on_response(resp):
        if "generic.do" not in resp.url:
            return
        try:
            body = resp.text()
            if "<LR" in body:
                captured.append(body)
                print(f"  Captured XHR with data ({len(body)} bytes)")
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.on("response", on_response)

        print("[1] Navigating to M3 portal (SSO)...")
        page.goto(M3_PORTAL, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(6_000)

        print("[2] Opening search dialog...")
        cmd = page.locator("#cmdText")
        if not cmd.is_visible():
            page.evaluate(
                "$('#cmdText').parents().each(function(){$(this).show()});"
                "$('#cmdText').show().focus()"
            )
            page.wait_for_timeout(1_000)

        cmd.click()
        cmd.fill("")
        for char in "xdrx800":
            cmd.type(char, delay=150)
        page.wait_for_timeout(2_000)

        print("[3] Clicking OK → Transport Orders...")
        ok_btn = page.get_by_text("OK", exact=True).first
        if ok_btn.is_visible():
            ok_btn.click()
            page.wait_for_timeout(3_000)

        try:
            page.locator("text=Transport Orders").first.click()
        except Exception:
            page.locator("a:has-text('Transport')").first.click()
        page.wait_for_timeout(8_000)

        # Find the XDRX800 iframe
        xdrx_frame = None
        for frame in page.frames:
            try:
                if "DTHID" in frame.content():
                    xdrx_frame = frame
                    break
            except Exception:
                continue

        if not xdrx_frame:
            print("ERROR: XDRX800 iframe not found!")
            page.screenshot(path=str(MOCK_DIR / "capture_error.png"))
            browser.close()
            return 1

        print("[4] XDRX800 loaded — searching for your TOs...")
        # Search with your username, broad status range, no TO filter
        snac = xdrx_frame.locator('input[name="DTHSNAC"]')
        if snac.is_visible():
            snac.fill("TMOGHANAN")

        stat2 = xdrx_frame.locator('input[name="DTHSTAT2"]')
        if stat2.is_visible():
            stat2.fill("89")

        # Clear DTHID to get all TOs
        dthid = xdrx_frame.locator('input[name="DTHID"]')
        if dthid.is_visible():
            dthid.fill("")
            dthid.press("Enter")

        page.wait_for_timeout(5_000)

        # Save captured responses
        if captured:
            out_path = MOCK_DIR / "to_all.xml"
            # Use the last (most complete) response
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(captured[-1])
            print(f"\nSaved {out_path} ({len(captured[-1])} bytes)")

            # Quick parse to show what we got
            import xml.etree.ElementTree as ET

            root = ET.fromstring(captured[-1])
            to_count = sum(1 for _ in root.iter("LR"))
            print(f"Contains {to_count} TO row(s)")
        else:
            print("\nWARNING: No data captured. Check the page state.")
            page.screenshot(path=str(MOCK_DIR / "capture_nodata.png"))

        browser.close()

    print("\nDone. Commit mock_data/ and push to GitHub for VPS testing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
