"""
M3 H5 Client — Playwright-based browser automation for XDRX800 (Transport Orders).

This client automates the MNE web frontend because XDRX800 is an MNE-only
program with no REST API, no ODBC table, and no MvxMCSvt panel data.
The only way to query TO status programmatically is through the browser.

Key constraints:
  - Headless mode does NOT work (ADFS SSO requires headed Edge).
  - Ctrl+R refreshes the browser, not the search dialog — click #cmdText.
  - XHR response listener must try/except around .text() (redirects crash it).
  - XDRX800 runs inside an iframe from pfeash5live.pepperl-fuchs.com.

Usage:
    client = M3H5Client(config)
    client.connect()
    result = client.get_to_status("147715")
    results = client.get_multiple_to_status(["147715", "147297"])
    client.close()
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger("m3_h5_client")

M3_PORTAL = "https://pfde-m3-auth.eu.p-f.biz/mne/"
XDRX800_HOST = "pfeash5live.pepperl-fuchs.com"

# Column index → field name mapping for XDRX800 list rows.
# Derived from the <LCols> definition in the XDRX800 XML response.
COLUMN_MAP = {
    0: "to_number",
    1: "main_to",
    2: "lines",
    3: "status",
    4: "delivery_service",
    5: "responsible",
    6: "sending_site",
    7: "receiver",
    8: "receiving_site",
    9: "rec_country",
    10: "fta",
    11: "customer_no",
    12: "remark",
    13: "creation_date",
    14: "arrived_at_logistics",
    15: "matter_of_delivery",
    16: "temp_co",
    17: "co",
    18: "delivery",
    19: "reference_order",
}


def parse_xdrx800_xml(xml_text: str) -> list[dict[str, Any]]:
    """
    Parse XDRX800 generic.do XML response into a list of TO record dicts.

    Each <LR> element in <LRows> becomes one dict. Column cells (<LC>) are
    mapped to named fields via COLUMN_MAP. The raw status string (e.g.
    "44 - Shipped from sending site") is also split into status_code and
    status_description for easier downstream use.

    Returns an empty list if no <LRows> or <LR> elements are found.
    """
    if not xml_text or "<LR" not in xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Failed to parse XDRX800 XML response")
        return []

    rows = []
    # LRows can be nested under Panels/Panel/Objs/List/LView/LRows
    for lr in root.iter("LR"):
        row: dict[str, Any] = {}
        row_name = lr.get("name", "")  # e.g. "R1"

        for lc in lr.findall("LC"):
            cell_name = lc.get("name", "")  # e.g. "R1C3"
            # Extract column index from cell name: R1C3 → 3
            try:
                col_idx = int(cell_name.split("C")[-1])
            except (ValueError, IndexError):
                continue

            field_name = COLUMN_MAP.get(col_idx)
            if field_name:
                row[field_name] = (lc.text or "").strip()

        # Split status into code + description for convenience
        raw_status = row.get("status", "")
        if " - " in raw_status:
            parts = raw_status.split(" - ", 1)
            row["status_code"] = parts[0].strip()
            row["status_description"] = parts[1].strip()
        else:
            row["status_code"] = raw_status
            row["status_description"] = ""

        if row.get("to_number"):
            rows.append(row)

    return rows


class M3H5Client:
    """
    Playwright-based client for M3 XDRX800 (Transport Orders).

    Launches Edge in headed mode, authenticates via ADFS SSO, opens XDRX800,
    and provides methods to look up TO status by number.

    In mock mode, reads from saved XML files in mock_data_dir instead of
    launching a browser.
    """

    def __init__(self, config, mock_data_dir: Path | None = None):
        self.config = config
        self.is_mock = getattr(config, "is_mock", False) or (config.mode == "mock")
        self.mock_data_dir = mock_data_dir
        self._playwright = None
        self._browser = None
        self._page = None
        self._xdrx_frame = None
        self._connected = False
        self._captured_responses: list[str] = []

    # ── Connection ──────────────────────────────────────────────────────

    def connect(self) -> None:
        """
        Launch browser, SSO-authenticate, and open XDRX800.

        In mock mode this is a no-op.
        """
        if self.is_mock:
            logger.info("M3 H5 Client: mock mode — no browser launched")
            self._connected = True
            return

        from playwright.sync_api import sync_playwright

        logger.info("Launching Edge for M3 XDRX800...")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            channel="msedge", headless=False
        )
        context = self._browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
        )
        self._page = context.new_page()

        # XHR capture — safe handler that skips redirect responses
        self._captured_responses.clear()
        self._page.on("response", self._on_response)

        # Step 1: Navigate to M3 portal (SSO auto-authenticates)
        logger.info("Navigating to M3 portal (SSO)...")
        self._page.goto(M3_PORTAL, timeout=60_000, wait_until="domcontentloaded")
        self._page.wait_for_timeout(6_000)

        # Step 2: Open XDRX800 via search dialog
        self._open_xdrx800()

        # Step 3: Find the XDRX800 iframe
        self._xdrx_frame = self._find_xdrx_frame()
        if not self._xdrx_frame:
            raise RuntimeError(
                "XDRX800 iframe not found after opening program. "
                "Check debug_m3_xdrx800_open.png for the page state."
            )

        # Step 4: Clear the Responsible field so we can search by TO only
        self._clear_responsible_field()

        self._connected = True
        logger.info("M3 H5 Client connected — XDRX800 ready")

    def close(self) -> None:
        """Shut down browser and Playwright."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._connected = False
        logger.info("M3 H5 Client closed")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Public API ──────────────────────────────────────────────────────

    def get_to_status(self, to_number: str) -> dict[str, Any] | None:
        """
        Look up a single TO by number. Returns the first matching row as a
        dict, or None if not found.

        In mock mode, reads from mock_data_dir/to_{to_number}.xml.
        """
        if not self._connected:
            raise RuntimeError("Call connect() before querying")

        if self.is_mock:
            return self._mock_lookup(to_number)

        return self._live_lookup(to_number)

    def get_multiple_to_status(
        self, to_numbers: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """
        Look up multiple TOs. Returns {to_number: result_dict_or_None}.

        Reuses the same browser session for all lookups.
        """
        results: dict[str, dict[str, Any] | None] = {}
        for i, to_num in enumerate(to_numbers):
            logger.info(
                "Looking up TO %s (%d/%d)...", to_num, i + 1, len(to_numbers)
            )
            try:
                results[to_num] = self.get_to_status(to_num)
            except Exception as exc:
                logger.error("Failed to look up TO %s: %s", to_num, exc)
                results[to_num] = None
        return results

    # ── Browser automation internals ────────────────────────────────────

    def _on_response(self, response) -> None:
        """
        Capture XHR responses from generic.do.

        CRITICAL: response.text() crashes on redirect responses (3xx).
        Always wrap in try/except.
        """
        if "generic.do" not in response.url:
            return
        try:
            body = response.text()
            if "<LR" in body or "<LRows" in body or "<Panel" in body:
                self._captured_responses.append(body)
        except Exception:
            pass  # Skip redirect or binary responses

    def _open_xdrx800(self) -> None:
        """
        Open XDRX800 via the M3 portal search dialog.

        Matches the proven phase_b_pw9.py flow:
          1. Dispatch Ctrl+R via KeyboardEvent to surface the search box
          2. Force-show #cmdText if hidden, click + fill empty
          3. Type 'xdrx800' char-by-char so autocomplete fires
          4. Wait for dropdown, then click Transport Orders directly
             (fall back to an OK button if the link isn't visible yet)
        """
        page = self._page
        logger.info("Opening search dialog...")

        # Step 1: Dispatch Ctrl+R to open search (Ctrl+R in the portal is
        # intercepted — we send a synthetic KeyboardEvent so the browser
        # doesn't actually reload the page).
        page.evaluate(
            """
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'r', ctrlKey: true, bubbles: true
            }));
            """
        )
        page.wait_for_timeout(2_000)

        # Step 2: Ensure #cmdText is visible (force-show via jQuery if not)
        cmd = page.locator("#cmdText")
        if not cmd.is_visible():
            page.evaluate(
                "$('#cmdText').parents().each(function(){$(this).show()});"
                "$('#cmdText').show().focus()"
            )
            page.wait_for_timeout(1_000)

        # Step 3: Click, clear, and type xdrx800 char-by-char for autocomplete
        cmd.click()
        cmd.fill("")
        for char in "xdrx800":
            cmd.type(char, delay=200)

        # Step 4: Wait for autocomplete dropdown — do NOT press Enter
        page.wait_for_timeout(3_000)
        page.screenshot(path="debug_m3_search_results.png", full_page=True)

        # Step 5+6: Prefer the Transport Orders entry directly
        transport_link = page.locator("text=Transport Orders").first
        if transport_link.is_visible():
            transport_link.click()
        else:
            # Step 7: Not visible yet — try an OK button, wait, then look again
            logger.info("Transport Orders not visible — trying OK fallback")
            try:
                body_preview = page.inner_text("body")[:1000]
                logger.warning(
                    "Visible body (first 1000 chars):\n%s", body_preview
                )
            except Exception as exc:
                logger.warning("Could not read page body: %s", exc)

            ok_btn = page.get_by_text("OK", exact=True).first
            if ok_btn.is_visible():
                ok_btn.click()
                page.wait_for_timeout(3_000)

            transport_link = page.locator("text=Transport Orders").first
            if transport_link.is_visible():
                transport_link.click()
            else:
                # Last-ditch fallback: any link containing "Transport"
                page.locator("a:has-text('Transport')").first.click()

        # Step 8: Give the iframe time to load
        page.wait_for_timeout(15_000)
        page.screenshot(path="debug_m3_xdrx800_open.png")
        logger.info("XDRX800 program opened")

    def _find_xdrx_frame(self):
        """
        Find the iframe containing XDRX800 form fields.

        Polls page.frames every 2s for up to 20s — the iframe can be slow
        to load after the Transport Orders click.
        """
        deadline = time.time() + 20
        while time.time() < deadline:
            for frame in self._page.frames:
                try:
                    content = frame.content()
                    if "DTHID" in content or "DTHSNAC" in content:
                        logger.info(
                            "Found XDRX800 iframe: %s", frame.url[:80]
                        )
                        return frame
                except Exception:
                    continue
            time.sleep(2)
        return None

    def _clear_responsible_field(self) -> None:
        """
        Clear the Responsible (DTHSNAC) field so searches aren't scoped
        to the logged-in user only.
        """
        frame = self._xdrx_frame
        snac = frame.locator('input[name="DTHSNAC"]')
        if snac.is_visible():
            snac.fill("")
            logger.debug("Cleared DTHSNAC field")

    def _live_lookup(self, to_number: str) -> dict[str, Any] | None:
        """
        Look up a TO by number in the live XDRX800 interface.

        Sets the DTHID filter field, presses Enter, waits for XHR,
        parses the response.
        """
        frame = self._xdrx_frame
        if not frame:
            raise RuntimeError("XDRX800 iframe lost — cannot query")

        # Clear previous captures
        self._captured_responses.clear()

        # Fill the TO number filter field
        dthid = frame.locator('input[name="DTHID"]')
        if not dthid.is_visible():
            logger.warning("DTHID input not visible in XDRX800 frame")
            return None

        dthid.fill(to_number)
        dthid.press("Enter")

        # Wait for XHR response (up to 10 seconds)
        deadline = time.time() + 10
        while time.time() < deadline:
            if self._captured_responses:
                break
            time.sleep(0.3)

        if not self._captured_responses:
            logger.warning("No XHR response for TO %s (timeout)", to_number)
            return None

        # Parse the most recent response that has data rows
        for xml_text in reversed(self._captured_responses):
            rows = parse_xdrx800_xml(xml_text)
            if rows:
                # Find the exact TO match
                for row in rows:
                    if row.get("to_number") == to_number:
                        return row
                # If no exact match, return first row (might be padded differently)
                logger.debug(
                    "No exact match for TO %s in %d rows, returning first",
                    to_number,
                    len(rows),
                )
                return rows[0]

        logger.info("TO %s not found in XDRX800", to_number)
        return None

    # ── Mock mode ───────────────────────────────────────────────────────

    def _mock_lookup(self, to_number: str) -> dict[str, Any] | None:
        """
        Read a saved XML response from mock_data/to_{to_number}.xml.

        Falls back to mock_data/to_all.xml if per-TO file doesn't exist.
        """
        if not self.mock_data_dir:
            logger.warning("Mock mode but no mock_data_dir set")
            return None

        # Try per-TO file first
        to_file = self.mock_data_dir / f"to_{to_number}.xml"
        if not to_file.exists():
            # Fall back to aggregate file
            to_file = self.mock_data_dir / "to_all.xml"
        if not to_file.exists():
            logger.debug("No mock data for TO %s", to_number)
            return None

        xml_text = to_file.read_text(encoding="utf-8")
        rows = parse_xdrx800_xml(xml_text)

        # Find exact match in parsed rows
        for row in rows:
            if row.get("to_number") == to_number:
                return row

        # If file is per-TO, return first row
        if rows and f"to_{to_number}" in to_file.name:
            return rows[0]

        return None
