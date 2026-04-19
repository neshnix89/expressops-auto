"""
M3 H5 Client — XDRX800 Internal Shipments TO Status Lookup
============================================================
Queries the M3 MNE web application to get TO status from XDRX800.

Requirements:
    pip install requests requests_ntlm

Usage:
    from m3_h5_client import XDRX800Client
    client = XDRX800Client()
    status = client.get_to_status("147715")
    # Returns: {"to_no": "147715", "status": "44 - Shipped from sending site", ...}

Auth: Uses Windows NTLM (your domain credentials). No password needed
      if running as your logged-in Windows user.
"""

import requests
import xml.etree.ElementTree as ET
import re
import os
import json
from datetime import datetime

try:
    from requests_ntlm import HttpNtlmAuth
    HAS_NTLM = True
except ImportError:
    HAS_NTLM = False

try:
    from requests_negotiate_sspi import HttpNegotiateAuth
    HAS_SSPI = True
except ImportError:
    HAS_SSPI = False


# =============================================================================
# CONFIG
# =============================================================================
MNE_BASE = "https://pfeash5live.pepperl-fuchs.com"
GENERIC_DO = f"{MNE_BASE}/mwp/jsp/component/pfmodules/generic.do"

# XDRX800 list columns in order (from XML LCols)
LCOL_NAMES = [
    "DTHID",      # TO No.
    "DTHMAIN",    # Main TO
    "DTHLINES",   # Lines
    "DTHSTAT",    # Status (e.g. "44 - Shipped from sending site")
    "DTDSRV",     # Delivery Service
    "DTHSNAC1",   # Responsible
    "DTHSNSB",    # Sending Site
    "DTHTRAC",    # Receiver
    "DTHTRSB",    # Receiving site
    "PARM",       # Rec. country (ISO)
    "FTA",        # FTA
    "DTHCUNO",    # Customer No.
    "DTHDSC",     # Remark
    "DTHRGDT1",   # Creation Date
    "DTHDATE",    # Arrived at logistics
    "DTMAOD",     # Matter of Delivery
    "DTORNO",     # Temp.CO
    "EVORNR",     # CO
    "DTHDLIX",    # Delivery
    "DTHORNO",    # Reference Order
]


class XDRX800Client:
    """Client for querying M3 XDRX800 Internal Shipments via MNE H5 API."""

    def __init__(self, username=None, password=None, domain=None):
        """
        Initialize session with Windows auth.

        Args:
            username: Windows username (default: current user from env)
            password: Windows password (default: uses SSPI if available)
            domain: Windows domain (default: from USERDOMAIN env)
        """
        self.session = requests.Session()
        self.session.verify = False  # Corporate certs often not in Python's store
        self.sid = None
        self.iid = None

        # Suppress SSL warnings for corporate certs
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Set up auth
        if HAS_SSPI:
            # Best option: Windows SSPI (no password needed, uses logged-in user)
            self.session.auth = HttpNegotiateAuth()
            self._auth_method = "SSPI"
        elif HAS_NTLM:
            # Fallback: NTLM with explicit credentials
            domain = domain or os.environ.get("USERDOMAIN", "PEPPERL-FUCHS")
            username = username or os.environ.get("USERNAME", "")
            if not password:
                raise ValueError(
                    "NTLM auth requires password. Either:\n"
                    "  1. pip install requests_negotiate_sspi  (uses Windows login, no password)\n"
                    "  2. Pass password= to XDRX800Client()\n"
                    "  3. Set M3_PASSWORD env variable"
                )
            self.session.auth = HttpNtlmAuth(f"{domain}\\{username}", password)
            self._auth_method = "NTLM"
        else:
            raise ImportError(
                "No auth library available. Install one:\n"
                "  pip install requests_negotiate_sspi   (recommended, no password needed)\n"
                "  pip install requests_ntlm             (needs password)"
            )

    def _init_program(self):
        """Initialize XDRX800 program — equivalent to opening it in the browser."""
        # Step 1: Get the MNE landing page to establish session
        resp = self.session.get(f"{MNE_BASE}/mwp/", timeout=30)
        resp.raise_for_status()

        # Step 2: Init XDRX800 program
        params = {
            "keytype": "DEF",
            "applicationtype": "DRX800B",
            "application": "drx800",
            "H5": "true",
            "IID": "NEW",
        }
        resp = self.session.post(GENERIC_DO, params=params, timeout=30)
        resp.raise_for_status()

        # Parse session data from XML response
        root = ET.fromstring(resp.text)
        session_node = root.find(".//SessionData")
        if session_node is not None:
            self.sid = session_node.findtext("SID", "")
            self.iid = session_node.findtext("IID", "0")
        else:
            # Try to get SID from cookie
            self.sid = self.session.cookies.get("JSESSIONID", "")
            self.iid = "0"

        return resp.text

    def _search(self, to_number=None, responsible=None, status_from="", status_to="89"):
        """
        Execute XDRX800 search.

        Args:
            to_number: Specific TO number to look up (optional)
            responsible: Filter by responsible person (optional)
            status_from: Min status code (default: empty = all)
            status_to: Max status code (default: 89 = exclude closed)

        Returns:
            Raw XML response text
        """
        if not self.sid:
            self._init_program()

        form_data = {
            "DTHSTAT1": status_from,
            "DTHSTAT2": status_to,
            "DTHDSC": "",
            "FROMRGDT": "",
            "DTHRGDT": datetime.now().strftime("%Y-%m-%d"),
            "B_DTHSUB": "1",
            "DTHSNSB": "",
            "DTHTRSB": "",
            "DTMAOD": "",
            "E_DTDSRV": "",
            "DTHSNAC": responsible or "",
            "DTHTRAC": "",
            "DefOpt": "5",
            "Title": "P+F Enterprise Application Server",
            "User": os.environ.get("USERNAME", "TMOGHANAN"),
            "Cmp": "1",
            "Divi": "Pepperl+Fuchs Asia Pte. Ltd.",
            "Lng": "SI",
            "ERPV": "5.1.990.workplace",
            "Skin": "std",
            "Ver": "5.1.4",
            "Meth": "POST",
            "helpURL": "/mnehelp/SI",
            "ShowIcons": "true",
            "TabPlus": "false",
            "dSep": ".",
            "tSep": ",",
            "OptCols": "false",
            "focusField": "DTHID",
            "applicationtype": "DRX800B",
            "submiturl": "/generic.do",
            "application": "drx800",
            "keytype": "ENTER",
            "URL": "",
            "SID": self.sid,
            "IID": self.iid,
            "SNR": "",
            "CMDTP": "KEY",
            "CMDVAL": "ENTER",
            "FCS": "",
            "SELROWS": "",
            "PURL": "",
            "BROWSEINQTYPE": "10",
            "DTHID": f"{to_number} " if to_number else "",
            "DTHMAIN": "",
            "A.CTPARM": "",
            "B.CTPARM": "",
            "DTORNO": "",
            "EVORNR": "",
            "URDLIX": "",
            "H5": "true",
        }

        resp = self.session.post(GENERIC_DO, data=form_data, timeout=30)
        resp.raise_for_status()
        return resp.text

    def _parse_rows(self, xml_text):
        """Parse TO data rows from XDRX800 XML response."""
        root = ET.fromstring(xml_text)
        rows = []

        # Try multiple possible row container formats
        lrows = root.find(".//LRows")
        if lrows is None:
            return rows

        for lrow in lrows.findall("LRow"):
            row_data = {}
            fields = lrow.findall("LFld")
            if fields:
                # Format: <LFld name="DTHID">147715</LFld>
                for fld in fields:
                    name = fld.get("name", "")
                    row_data[name] = (fld.text or "").strip()
            else:
                # Format: <LCol>value</LCol> in order
                cols = lrow.findall("LCol")
                if not cols:
                    # Format: direct text children or <C> elements
                    cols = lrow.findall("C")
                if cols:
                    for i, col in enumerate(cols):
                        if i < len(LCOL_NAMES):
                            row_data[LCOL_NAMES[i]] = (col.text or "").strip()

            if row_data:
                rows.append(row_data)

        return rows

    def get_to_status(self, to_number):
        """
        Look up a single TO status.

        Args:
            to_number: TO number as string (e.g. "147715")

        Returns:
            dict with TO details, or None if not found.
            Keys: to_no, status, status_code, delivery_service, responsible,
                  sending_site, receiver, receiving_site, remark, creation_date,
                  arrived_date, matter_of_delivery
        """
        xml_text = self._search(to_number=to_number, status_from="", status_to="99")
        rows = self._parse_rows(xml_text)

        if not rows:
            # Debug: save raw XML for inspection
            debug_path = f"debug_xdrx800_{to_number}.xml"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(xml_text)
            return None

        # Find the matching row (there should be exactly one for a specific TO search)
        for row in rows:
            to_id = row.get("DTHID", "").strip()
            if to_id == to_number or to_id == to_number.lstrip("0"):
                status_raw = row.get("DTHSTAT", "")
                status_code = status_raw.split(" ")[0] if status_raw else ""
                return {
                    "to_no": to_id,
                    "status": status_raw,
                    "status_code": status_code,
                    "delivery_service": row.get("DTDSRV", ""),
                    "responsible": row.get("DTHSNAC1", ""),
                    "sending_site": row.get("DTHSNSB", ""),
                    "receiver": row.get("DTHTRAC", ""),
                    "receiving_site": row.get("DTHTRSB", ""),
                    "remark": row.get("DTHDSC", ""),
                    "creation_date": row.get("DTHRGDT1", ""),
                    "arrived_date": row.get("DTHDATE", ""),
                    "matter_of_delivery": row.get("DTMAOD", ""),
                    "customer_no": row.get("DTHCUNO", ""),
                }

        # If no exact match, return first row (single-TO search usually returns one)
        if rows:
            row = rows[0]
            status_raw = row.get("DTHSTAT", "")
            status_code = status_raw.split(" ")[0] if status_raw else ""
            return {
                "to_no": row.get("DTHID", to_number),
                "status": status_raw,
                "status_code": status_code,
                "delivery_service": row.get("DTDSRV", ""),
                "responsible": row.get("DTHSNAC1", ""),
                "sending_site": row.get("DTHSNSB", ""),
                "receiver": row.get("DTHTRAC", ""),
                "receiving_site": row.get("DTHTRSB", ""),
                "remark": row.get("DTHDSC", ""),
                "creation_date": row.get("DTHRGDT1", ""),
                "arrived_date": row.get("DTHDATE", ""),
                "matter_of_delivery": row.get("DTMAOD", ""),
                "customer_no": row.get("DTHCUNO", ""),
            }

        return None

    def get_multiple_to_status(self, to_numbers):
        """
        Look up multiple TOs. Queries each individually.

        Args:
            to_numbers: list of TO number strings

        Returns:
            dict mapping TO number → status dict (or None if not found)
        """
        results = {}
        for to_no in to_numbers:
            try:
                results[to_no] = self.get_to_status(to_no)
            except Exception as e:
                results[to_no] = {"error": str(e)}
        return results


# =============================================================================
# TEST / DISCOVERY
# =============================================================================
def test_connection():
    """
    Test script — run this first to verify auth and see raw XML format.

    Usage:
        python m3_h5_client.py
    """
    print("=" * 60)
    print("XDRX800 H5 Client — Connection Test")
    print("=" * 60)

    # Check available auth
    print(f"\nAuth libraries:")
    print(f"  requests_negotiate_sspi: {'YES' if HAS_SSPI else 'NO'}")
    print(f"  requests_ntlm: {'YES' if HAS_NTLM else 'NO'}")

    if not HAS_SSPI and not HAS_NTLM:
        print("\n⚠ No auth library found. Install one:")
        print("  pip install requests_negotiate_sspi")
        print("  pip install requests_ntlm")
        return

    try:
        client = XDRX800Client()
        print(f"\n[1] Auth method: {client._auth_method}")

        print(f"[2] Initializing XDRX800...")
        init_xml = client._init_program()
        print(f"    SID: {client.sid}")
        print(f"    IID: {client.iid}")

        # Save init response for debugging
        with open("debug_xdrx800_init.xml", "w", encoding="utf-8") as f:
            f.write(init_xml)
        print(f"    Init XML saved to debug_xdrx800_init.xml")

        print(f"[3] Searching for TO 147715...")
        search_xml = client._search(to_number="147715", status_to="99")

        # Save search response
        with open("debug_xdrx800_search.xml", "w", encoding="utf-8") as f:
            f.write(search_xml)
        print(f"    Search XML saved to debug_xdrx800_search.xml")

        # Try to parse rows
        rows = client._parse_rows(search_xml)
        print(f"    Rows found: {len(rows)}")
        for i, row in enumerate(rows):
            print(f"    Row {i+1}: {json.dumps(row, indent=6)}")

        if not rows:
            print(f"\n    No rows parsed. Check debug_xdrx800_search.xml")
            print(f"    Look for <LRows> section — the row format might differ.")
            # Show a snippet around LRows
            if "<LRows" in search_xml:
                start = search_xml.index("<LRows")
                snippet = search_xml[start:start+500]
                print(f"\n    LRows snippet:\n{snippet}")
            else:
                print(f"\n    No <LRows> found in response at all.")

        # Also try a broader search by responsible
        print(f"\n[4] Searching by Responsible=TMOGHANAN (status 1-89)...")
        broad_xml = client._search(responsible="TMOGHANAN", status_to="89")
        with open("debug_xdrx800_broad.xml", "w", encoding="utf-8") as f:
            f.write(broad_xml)

        broad_rows = client._parse_rows(broad_xml)
        print(f"    Rows found: {len(broad_rows)}")
        for i, row in enumerate(broad_rows[:5]):
            print(f"    Row {i+1}: {json.dumps(row, indent=6)}")

        print(f"\n{'=' * 60}")
        print("Test complete. Debug XML files saved for inspection.")
        print("Paste debug_xdrx800_search.xml contents back if rows are empty.")
        print(f"{'=' * 60}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_connection()
