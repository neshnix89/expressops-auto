"""
M3 H5 Client — Playwright-based browser automation for M3 programs (XDRX800, XECX450).

connect() launches Edge and authenticates via ADFS SSO. It does NOT open any
specific M3 program. Individual programs are opened lazily on first use:
  - XDRX800 (Transport Orders) is opened by the first get_to_status() call.
  - XECX450 (Product Locks/Releases) is opened by each get_e5_release_status() call.

Both programs share the same browser session and page — opened sequentially via
the M3 portal search dialog.

Key constraints:
  - Headless mode does NOT work (ADFS SSO requires headed Edge).
  - Ctrl+R refreshes the browser, not the search dialog — click #cmdText.
  - XHR response listener must try/except around .text() (redirects crash it).
  - XDRX800 runs inside an iframe from pfeash5live.pepperl-fuchs.com.

Usage:
    client = M3H5Client(config)
    client.connect()
    result = client.get_to_status("147715")           # opens XDRX800 on first call
    results = client.get_multiple_to_status(["147715", "147297"])
    status = client.get_e5_release_status("1234567")  # opens XECX450 on each call
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


# Column index → field name mapping for XECX450 list rows.
ECX450_COLUMN_MAP = {
    0: "item_number",   # PHPRNO
    1: "facility",      # PHFACI
    2: "structure_type",  # PHSTRT
    9: "rnd1_status",   # LHSTA1
    10: "rnd2_status",  # LHSTA2
    11: "production_status",  # LHSTA3
}


def parse_ecx450_xml(xml_text: str) -> dict[str, Any]:
    """
    Parse XECX450 generic.do XML response for release status at MF1/STD.

    Finds the row where facility=MF1 and structure_type=STD, then reads
    the style attribute on C9/C10/C11: "HIGHINTGR" means Released (True).

    Returns a result dict or {"error": "<reason>"} if the row is not found.
    """
    if not xml_text or "<LR" not in xml_text:
        return {"error": "empty or non-list XML response"}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Failed to parse XECX450 XML response")
        return {"error": "XML parse error"}

    for lr in root.iter("LR"):
        cells: dict[int, tuple[str, str]] = {}  # col_idx → (text, style)
        for lc in lr.findall("LC"):
            cell_name = lc.get("name", "")
            try:
                col_idx = int(cell_name.split("C")[-1])
            except (ValueError, IndexError):
                continue
            cells[col_idx] = ((lc.text or "").strip(), lc.get("style", ""))

        facility = cells.get(1, ("", ""))[0]
        structure_type = cells.get(2, ("", ""))[0]

        if facility != "MF1" or structure_type != "STD":
            continue

        return {
            "item_number": cells.get(0, ("", ""))[0],
            "facility": facility,
            "structure_type": structure_type,
            "rnd1_released": cells.get(9, ("", ""))[1] == "HIGHINTGR",
            "rnd2_released": cells.get(10, ("", ""))[1] == "HIGHINTGR",
            "production_released": cells.get(11, ("", ""))[1] == "HIGHINTGR",
        }

    return {"error": "MF1/STD structure not found"}


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
    Playwright-based client for M3 programs (XDRX800, XECX450).

    connect() launches Edge in headed mode and authenticates via ADFS SSO.
    M3 programs are opened lazily on first use by their respective methods.

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
        self._xdrx_initialized = False  # True after XDRX800 is opened and filters cleared
        self._connected = False
        self._captured_responses: list[str] = []

    # ── Connection ──────────────────────────────────────────────────────

    def connect(self) -> None:
        """
        Launch browser and SSO-authenticate against the M3 portal.

        Does NOT open any specific M3 program. Programs are opened lazily
        on first use by get_to_status() and get_e5_release_status().

        In mock mode this is a no-op.
        """
        if self.is_mock:
            logger.info("M3 H5 Client: mock mode — no browser launched")
            self._connected = True
            return

        from playwright.sync_api import sync_playwright

        logger.info("Launching Edge for M3...")
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

        # Navigate to M3 portal (SSO auto-authenticates)
        logger.info("Navigating to M3 portal (SSO)...")
        self._page.goto(M3_PORTAL, timeout=60_000, wait_until="domcontentloaded")
        self._page.wait_for_timeout(6_000)

        self._connected = True
        logger.info("M3 H5 Client connected — SSO complete, no program opened yet")

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

        Reuses the same browser session for all lookups. Any TO that comes
        back None on the first pass is retried once — the form is reliably
        stable after the first successful lookup, so transient misses
        (slow XHR, dropdown timing) usually resolve on a second attempt.
        """
        results: dict[str, dict[str, Any] | None] = {}

        # First pass
        for i, to_num in enumerate(to_numbers):
            logger.info(
                "Looking up TO %s (%d/%d)...", to_num, i + 1, len(to_numbers)
            )
            try:
                results[to_num] = self.get_to_status(to_num)
            except Exception as exc:
                logger.error("Failed to look up TO %s: %s", to_num, exc)
                results[to_num] = None

        # Retry failures once
        failed = [t for t, v in results.items() if v is None]
        if failed:
            logger.info("Retrying %d failed TO(s)...", len(failed))
            for to_num in failed:
                try:
                    results[to_num] = self.get_to_status(to_num)
                except Exception as exc:
                    logger.error("Retry failed for TO %s: %s", to_num, exc)

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

        Line-for-line port of phase_b_pw9.py. Every wait, screenshot, and
        diagnostic is preserved — do not simplify.
        """
        page = self._page

        # [2] Search dialog
        logger.info("Search dialog...")
        page.evaluate("""
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'r', code: 'KeyR', keyCode: 82, which: 82,
                ctrlKey: true, bubbles: true, cancelable: true
            }));
        """)
        page.wait_for_timeout(2000)
        if not page.locator("#cmdText").is_visible():
            page.evaluate(
                "$('#cmdText').parents().each(function(){$(this).show()}); "
                "$('#cmdText').show().focus()"
            )
            page.wait_for_timeout(1000)

        # [3] Typing xdrx800 slowly — char-by-char so autocomplete fires
        logger.info("Typing xdrx800 slowly...")
        cmd = page.locator("#cmdText")
        cmd.click()
        cmd.fill("")
        for char in "xdrx800":
            cmd.type(char, delay=200)
        page.wait_for_timeout(3000)
        page.screenshot(path="debug_pw_autocomplete.png")

        # [4] Look for autocomplete dropdown or results
        logger.info("Looking for results...")
        all_text = page.inner_text("body")
        has_transport = "Transport Orders" in all_text
        has_internal = "Internal Shipments" in all_text
        has_xdrx = "XDRX800" in all_text
        logger.info("  Transport Orders visible: %s", has_transport)
        logger.info("  Internal Shipments visible: %s", has_internal)
        logger.info("  XDRX800 in text: %s", has_xdrx)

        if not has_transport:
            # Click OK first, then look
            logger.info("  No results yet. Clicking OK...")
            page.get_by_text("OK", exact=True).first.click()
            page.wait_for_timeout(3000)
            page.screenshot(path="debug_pw_after_ok.png")

            has_transport = "Transport Orders" in page.inner_text("body")
            logger.info("  Transport Orders after OK: %s", has_transport)

            if not has_transport:
                # Results may be inside a popup/overlay or frame
                logger.info("  Checking all frames and overlays...")
                for frame in page.frames:
                    ft = frame.content()
                    if "Transport Orders" in ft:
                        logger.info("  Found in frame: %s", frame.url[:60])
                        has_transport = True

                # Also check for XDRX800 links specifically
                links = page.locator("a").all()
                for link in links:
                    try:
                        txt = link.inner_text().strip()
                        if "XDRX800" in txt or "Transport" in txt:
                            logger.info(
                                "  Link found: '%s' visible=%s",
                                txt,
                                link.is_visible(),
                            )
                            href = link.get_attribute("href") or ""
                            onclick = link.get_attribute("onclick") or ""
                            logger.info(
                                "    href=%s onclick=%s",
                                href[:60],
                                onclick[:60],
                            )
                    except Exception:
                        pass

        # [5] Try to click Transport Orders
        # JS click bypasses Playwright's visibility check — the link is in the
        # DOM but hidden behind the search dialog overlay.
        if has_transport:
            logger.info("Clicking XDRX800 link via JS (may be hidden by search overlay)...")
            try:
                page.locator("a[data-m3-link*='xdrx800']").first.evaluate("el => el.click()")
            except Exception:
                try:
                    page.locator("text=Transport Orders").first.click()
                except Exception:
                    page.locator("a:has-text('Transport')").first.click()
            page.wait_for_timeout(8000)
            page.screenshot(path="debug_pw_xdrx800.png")
        else:
            logger.warning("Cannot find Transport Orders anywhere.")
            logger.warning(
                "Full page text:\n%s", page.inner_text("body")[:1000]
            )

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

    def _clear_all_filters(self) -> None:
        """
        Clear every pre-populated filter on the XDRX800 form so only the
        DTHID (TO number) we type later drives the result set.

        Each field is wrapped in its own try/except — missing fields are
        skipped silently. Logs a count of how many were actually cleared.
        """
        frame = self._xdrx_frame
        text_fields = [
            "DTHSNAC",
            "DTHTRAC",
            "DTHSTAT1",
            "DTHSTAT2",
            "FROMRGDT",
            "DTHRGDT",
            "DTHDSC",
        ]

        cleared = 0
        for name in text_fields:
            try:
                el = frame.locator(f'input[name="{name}"]')
                if el.is_visible():
                    el.fill("")
                    cleared += 1
                    logger.debug("Cleared %s", name)
            except Exception as exc:
                logger.debug("Skipped %s: %s", name, exc)

        # Uncheck "Hide sub TO's" if it's currently checked
        try:
            sub = frame.locator('input[name="B_DTHSUB"]')
            if sub.is_visible() and sub.is_checked():
                sub.uncheck()
                cleared += 1
                logger.debug("Unchecked B_DTHSUB (Hide sub TOs)")
        except Exception as exc:
            logger.debug("Skipped B_DTHSUB: %s", exc)

        logger.info("XDRX800 filters cleared: %d field(s)", cleared)

        # Flush the cleared form: press Enter on DTHID so the server state
        # matches "no filters" before the first real TO lookup. Drop the
        # resulting XHR so it doesn't collide with the next query.
        try:
            frame.locator('input[name="DTHID"]').press("Enter")
            self._page.wait_for_timeout(5000)
            self._captured_responses.clear()
            logger.debug("Flushed XDRX800 form with empty filters")
        except Exception as exc:
            logger.warning("Could not flush XDRX800 form: %s", exc)

    def _live_lookup(self, to_number: str) -> dict[str, Any] | None:
        """
        Look up a TO by number in the live XDRX800 interface.

        Opens XDRX800 and clears filters on the first call, then reuses the
        browser session for subsequent lookups. Sets the DTHID filter field,
        presses Enter, waits for XHR, parses the response.
        """
        if not self._xdrx_initialized:
            self._open_xdrx800()
            self._xdrx_frame = self._find_xdrx_frame()
            if not self._xdrx_frame:
                raise RuntimeError(
                    "XDRX800 iframe not found after opening program. "
                    "Check debug_pw_xdrx800.png for the page state."
                )
            self._clear_all_filters()
            self._xdrx_initialized = True
            logger.info("XDRX800 initialized and ready")

        # The iframe reference goes stale after filter-reset submissions —
        # re-resolve it each lookup and update the cached handle.
        frame = self._find_xdrx_frame()
        if not frame:
            raise RuntimeError("XDRX800 iframe lost — cannot query")
        self._xdrx_frame = frame

        # Clear previous captures
        self._captured_responses.clear()

        # Fill the TO number filter field
        dthid = frame.locator('input[name="DTHID"]')
        if not dthid.is_visible():
            logger.warning("DTHID input not visible in XDRX800 frame")
            return None

        dthid.fill(to_number)
        dthid.press("Enter")

        # Give the form a moment to submit before we start polling captures
        self._page.wait_for_timeout(3000)

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

    # ── XECX450: Product Locks/Releases ────────────────────────────────

    def get_e5_release_status(self, item_number: str) -> dict[str, Any]:
        """
        Look up R&D1, R&D2, and Production release status for item_number
        at facility MF1, structure type STD via XECX450.

        In mock mode, reads from mock_data_dir/ecx450_{item_number}.xml.
        Returns a result dict or {"error": "<reason>"} on failure.
        """
        if not self._connected:
            raise RuntimeError("Call connect() before querying")

        if self.is_mock:
            return self._mock_ecx450_lookup(item_number)

        return self._live_ecx450_lookup(item_number)

    def _open_xecx450(self) -> None:
        """
        Open XECX450 via the M3 portal search dialog.

        Exact working sequence confirmed from live testing:
        1. Ctrl+R to open search dialog
        2. fill() 'xecx450' into #cmdText (not char-by-char)
        3. Click the OK button to submit
        4. Wait for the XECX450 iframe to attach
        """
        page = self._page

        logger.info("Search dialog (XECX450)...")
        page.evaluate("""
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'r', code: 'KeyR', keyCode: 82, which: 82,
                ctrlKey: true, bubbles: true, cancelable: true
            }));
        """)
        page.wait_for_timeout(2000)

        logger.info("Filling xecx450 in search box...")
        cmd = page.locator("#cmdText")
        cmd.click()
        cmd.fill("xecx450")

        page.locator('button:has-text("OK")').click()

        logger.info("Waiting for XECX450 iframe...")
        page.locator('iframe[src*="ecx450"]').wait_for(state="attached", timeout=30000)

        logger.info("XECX450 program opened")

    def _live_ecx450_lookup(self, item_number: str) -> dict[str, Any]:
        """
        Look up release status for item_number in the live XECX450 interface.

        Exact working sequence confirmed from live testing:
        - frame_locator() (not content_frame()) to access the iframe
        - input#PHPRNO (not [name=PHPRNO] which matches 2 elements)
        - Ctrl+A then char-by-char type to reliably replace any prior value
        - expect_response() instead of polling (time.sleep blocks event loop)
        - Enter to search (F5 refreshes the browser)
        """
        self._open_xecx450()

        ecx_frame = self._page.frame_locator('iframe[src*="ecx450"]')

        logger.info("Waiting for PHPRNO input...")
        ecx_frame.locator("input#PHPRNO").wait_for(state="visible", timeout=30000)
        time.sleep(3)  # let panel fully render before interacting

        phprno = ecx_frame.locator("input#PHPRNO")
        phprno.click()
        phprno.press("Control+a")
        for char in item_number:
            phprno.type(char, delay=100)

        logger.info("Submitting XECX450 query for item %s...", item_number)
        with self._page.expect_response(
            lambda r: "generic.do" in r.url and r.status == 200,
            timeout=30000,
        ) as resp_info:
            phprno.press("Enter")

        body = resp_info.value.text()
        return parse_ecx450_xml(body)

    # ── Mock mode ───────────────────────────────────────────────────────

    def _mock_ecx450_lookup(self, item_number: str) -> dict[str, Any]:
        """
        Read a saved XML response from mock_data/ecx450_{item_number}.xml.

        Falls back to mock_data/ecx450_all.xml if per-item file doesn't exist.
        """
        if not self.mock_data_dir:
            logger.warning("Mock mode but no mock_data_dir set")
            return {"error": "no mock_data_dir configured"}

        item_file = self.mock_data_dir / f"ecx450_{item_number}.xml"
        if not item_file.exists():
            item_file = self.mock_data_dir / "ecx450_all.xml"
        if not item_file.exists():
            logger.debug("No mock data for XECX450 item %s", item_number)
            return {"error": f"no mock data for item {item_number}"}

        xml_text = item_file.read_text(encoding="utf-8")
        return parse_ecx450_xml(xml_text)

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
