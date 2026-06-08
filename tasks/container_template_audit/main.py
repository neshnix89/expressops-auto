"""
NPI WorkContainer Auditor
=========================
Audits a JIRA NPI WorkContainer against the P+F NPI Toolchain procedure.

Usage:
    python npi_auditor.py NPIOTHER-123
    python npi_auditor.py PTDE-456 --json
    python npi_auditor.py NPIOTHER-123 --fix-hints

Configure credentials via environment variables or config.json (see bottom).
"""

import re
import sys
import json
import argparse
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config:
    # P+F JIRA on-prem — auth is Bearer PAT, NOT BasicAuth
    JIRA_BASE_URL = os.environ.get("JIRA_URL", "https://pfjira.pepperl-fuchs.com")
    JIRA_PAT      = os.environ.get("JIRA_PAT", "")   # Personal Access Token

    # Confirmed custom field IDs from expressops-auto project discovery
    # Source: expressops_auto "Automating daily tasks" + KPI pipeline chats
    FIELD_ORDER_TYPE      = "customfield_13905"   # NPI Order Type  e.g. "PR – Pilot Run"
    FIELD_PRODUCT_TYPE    = "customfield_13904"   # Product Type     e.g. "SMT PCBA"
    FIELD_NPI_LOCATION    = "customfield_13906"   # NPI Location     e.g. "Singapore"
    FIELD_REQUEST_TYPE    = "customfield_13903"   # Request Type     e.g. "NPI Request"
    FIELD_PTX_DOCUMENT    = "customfield_13907"   # PTxx Document
    FIELD_NPI_WC_STATUS   = "customfield_15400"   # NPI WC Status    "Red" / "Green"
    FIELD_PARKING_LOG     = "customfield_15800"   # Issue_parked_log
    FIELD_EDM_DOC_NUMBER  = "customfield_13300"   # EDM Document Number
    FIELD_CLONED_TEMPLATE = "customfield_12401"   # Cloned from Template Issue e.g. "ITPL-1027"
    # NOTE: customfield_13502 (M3 Article No) and customfield_15805 (Component Part No)
    # are ALWAYS EMPTY on containers — do not use for any lookup.
    # Project Number field ID is unconfirmed — uses JIRA project.key as fallback.

    # Optionally load PAT from config.yaml (reads jira.pat key) or config.json
    @classmethod
    def load_file(cls):
        # expressops-auto stores config in config/config.yaml
        yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config", "config.yaml"
        )
        if os.path.exists(yaml_path):
            try:
                import re as _re
                with open(yaml_path) as f:
                    raw = f.read()
                # Extract pat: from under jira: section
                jira_section = _re.search(r'jira:(.*?)(?=\n\w|\Z)', raw, _re.DOTALL)
                if jira_section:
                    m = _re.search(r'pat:\s*["\']?([^"\'#\n]+)', jira_section.group(1))
                    if m:
                        cls.JIRA_PAT = m.group(1).strip()
                    u = _re.search(r'base_url:\s*["\']?([^"\'#\n]+)', jira_section.group(1))
                    if u:
                        cls.JIRA_BASE_URL = u.group(1).strip()
            except Exception as e:
                print(f"Warning: could not read config.yaml: {e}")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Severity(Enum):
    ERROR   = "❌ ERROR"
    WARNING = "⚠️  WARN"
    INFO    = "ℹ️  INFO"
    OK      = "✅  OK"


@dataclass
class Finding:
    severity:  Severity
    category:  str
    message:   str
    fix_hint:  str = ""

    def __str__(self):
        line = f"  {self.severity.value}  [{self.category}]  {self.message}"
        if self.fix_hint:
            line += f"\n           → {self.fix_hint}"
        return line


@dataclass
class AuditReport:
    issue_key:  str
    summary:    str
    status:     str
    findings:   List[Finding] = field(default_factory=list)

    def add(self, severity, category, message, fix_hint=""):
        self.findings.append(Finding(severity, category, message, fix_hint))

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def passed(self):
        return [f for f in self.findings if f.severity == Severity.OK]

    def print(self, show_ok=True, show_fix=True):
        print("=" * 70)
        print(f"  NPI AUDIT REPORT — {self.issue_key}")
        print(f"  Summary : {self.summary}")
        print(f"  Status  : {self.status}")
        print("=" * 70)

        categories = {}
        for f in self.findings:
            categories.setdefault(f.category, []).append(f)

        for cat, items in categories.items():
            print(f"\n  ── {cat} ──")
            for item in items:
                if not show_ok and item.severity == Severity.OK:
                    continue
                if not show_fix:
                    item.fix_hint = ""
                print(item)

        print("\n" + "─" * 70)
        print(f"  Summary: {len(self.errors)} error(s) | "
              f"{len(self.warnings)} warning(s) | "
              f"{len(self.passed)} check(s) passed")
        if self.errors:
            print("\n  ⚡ This container has BLOCKING issues — requestor must fix before OPS can proceed.")
        elif self.warnings:
            print("\n  🔶 Container has warnings — OPS may need to chase requestor mid-run.")
        else:
            print("\n  🟢 Container looks complete. Ready to process.")
        print("=" * 70)

    def to_dict(self):
        return {
            "issue_key": self.issue_key,
            "summary":   self.summary,
            "status":    self.status,
            "findings": [
                {
                    "severity": f.severity.name,
                    "category": f.category,
                    "message":  f.message,
                    "fix_hint": f.fix_hint,
                }
                for f in self.findings
            ],
            "totals": {
                "errors":   len(self.errors),
                "warnings": len(self.warnings),
                "ok":       len(self.passed),
            }
        }


# ---------------------------------------------------------------------------
# JIRA client
# ---------------------------------------------------------------------------

class JiraClient:
    def __init__(self, base_url: str, pat: str):
        self.base    = base_url.rstrip("/")
        self.session = requests.Session()
        # P+F on-prem JIRA: Bearer PAT auth, self-signed cert → verify=False
        self.session.headers.update({
            "Authorization": f"Bearer {pat}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })
        self.session.verify = False   # on-prem self-signed cert — always False

    def get_issue(self, key: str) -> Dict[str, Any]:
        url = f"{self.base}/rest/api/2/issue/{key}"
        r = self.session.get(url, timeout=15)
        if r.status_code == 401:
            raise PermissionError("JIRA auth failed — check your PAT in config.yaml or JIRA_PAT env var.")
        if r.status_code == 404:
            raise ValueError(f"Issue {key} not found in JIRA.")
        r.raise_for_status()
        return r.json()

    def get_issue_links(self, key: str) -> List[Dict]:
        data = self.get_issue(key)
        return data.get("fields", {}).get("issuelinks", [])

    def discover_custom_fields(self) -> Dict[str, str]:
        """Helper: print all custom fields so you can confirm/update field IDs."""
        url = f"{self.base}/rest/api/2/field"
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        return {f["id"]: f["name"] for f in r.json() if f.get("custom")}


# ---------------------------------------------------------------------------
# Audit checks
# ---------------------------------------------------------------------------

BUILD_TYPES   = {"DS", "QS", "PT", "PR", "DMR"}
PRODUCT_TYPES = {"SMT PCBA", "Subassembly", "Final Good"}
ORDER_TYPES   = {"DS", "QS", "PT", "PR", "DMR"}
NPI_LOCATIONS = {"CZ2", "MF1", "SGP", "SG", "TN", "Trutnov", "Singapore"}

# Sections we expect to find in the template description
REQUIRED_SECTIONS = [
    "NPI Overview",
    "NPI Built Type",
    "Purpose of the NPI",
    "Focus Material",
    "Dual Use",
    "Instructions for the NPI",
    "Documents & Routing",
]

# Placeholder patterns in the template that indicate the section was NOT filled
PLACEHOLDER_PATTERNS = [
    r"#xxxxx?\b",          # part number placeholder
    r"\bxxxxxx?\b",        # generic placeholder
    r"##\b",               # quantity placeholder
    r"#####",              # forecast placeholder
    r"Click to add",       # Jira default empty
    r"Details filled by Requestor\s*$",  # section header with nothing below
]


class NPIAuditor:

    def __init__(self, client: JiraClient):
        self.client = client

    def audit(self, issue_key: str) -> AuditReport:
        raw    = self.client.get_issue(issue_key)
        fields = raw.get("fields", {})

        summary     = fields.get("summary", "") or ""
        status      = fields.get("status", {}).get("name", "Unknown")
        issue_type  = fields.get("issuetype", {}).get("name", "")
        description = fields.get("description", "") or ""
        assignee    = fields.get("assignee")
        reporter    = fields.get("reporter", {})
        project     = fields.get("project", {}).get("key", "")
        parent      = fields.get("parent", {})  # Project Parent
        components  = fields.get("components", [])
        labels      = fields.get("labels", [])

        # Custom fields
        order_type     = self._cf(fields, Config.FIELD_ORDER_TYPE)
        product_type   = self._cf(fields, Config.FIELD_PRODUCT_TYPE)
        npi_location   = self._cf(fields, Config.FIELD_NPI_LOCATION)
        request_type   = self._cf(fields, Config.FIELD_REQUEST_TYPE)
        ptx_document   = self._cf(fields, Config.FIELD_PTX_DOCUMENT)
        npi_wc_status  = self._cf(fields, Config.FIELD_NPI_WC_STATUS)
        parking_log    = self._cf(fields, Config.FIELD_PARKING_LOG)
        edm_doc_number = self._cf(fields, Config.FIELD_EDM_DOC_NUMBER)
        # Project Number: no confirmed field ID yet — use project key as proxy
        project_number = project

        report = AuditReport(
            issue_key=issue_key,
            summary=summary,
            status=status,
        )

        # Run all check groups
        self._check_issue_type(report, issue_type)
        self._check_project_parent(report, fields, parent, project)
        self._check_summary_nomenclature(report, summary)
        self._check_request_type(report, request_type, status)
        self._check_status_vs_content(report, status, description)
        self._check_core_fields(report, order_type, product_type, npi_location, project_number)
        self._check_ptx_for_build_type(report, order_type, ptx_document, summary)
        self._check_assignee(report, assignee, product_type, status)
        self._check_description_template(report, description, order_type, status)
        self._check_bom_release_hint(report, fields, description, status)
        self._check_parking(report, parking_log, status)
        self._check_wc_status_field(report, npi_wc_status)

        return report

    # ── field helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _cf(fields, field_id):
        """Extract custom field value (handles string, dict with 'value', or list)."""
        val = fields.get(field_id)
        if val is None:
            return None
        if isinstance(val, dict):
            return val.get("value") or val.get("name") or str(val)
        if isinstance(val, list):
            return ", ".join(
                (v.get("value") or v.get("name") or str(v)) if isinstance(v, dict) else str(v)
                for v in val
            )
        return str(val).strip()

    @staticmethod
    def _desc_contains(description: str, keyword: str) -> bool:
        return keyword.lower() in description.lower()

    @staticmethod
    def _extract_build_type_from_summary(summary: str) -> Optional[str]:
        parts = summary.strip().split()
        if parts and parts[0].upper() in BUILD_TYPES:
            return parts[0].upper()
        return None

    # ── individual checks ──────────────────────────────────────────────────

    def _check_issue_type(self, r: AuditReport, issue_type: str):
        cat = "Issue Type"
        if issue_type == "Work Container":
            r.add(Severity.OK, cat, "Issue type is 'Work Container'.")
        else:
            r.add(Severity.ERROR, cat,
                  f"Issue type is '{issue_type}', expected 'Work Container'.",
                  "Requestor must create this as a Work Container issue type, not a Task/Story/etc.")

    def _check_project_parent(self, r: AuditReport, fields, parent, project_key: str):
        cat = "Project Parent"
        pp_field = fields.get("customfield_10014") or parent  # common Jira epic link or parent
        parent_name = ""
        if isinstance(pp_field, dict):
            parent_name = pp_field.get("fields", {}).get("summary", "") or pp_field.get("key", "")
        elif isinstance(pp_field, str):
            parent_name = pp_field

        if parent_name:
            r.add(Severity.OK, cat, f"Project Parent set: {parent_name}")
        else:
            if "NPIOTHER" in project_key:
                r.add(Severity.INFO, cat,
                      "No Project Parent found. Container is in NPIOTHER — ensure parent is NPIOTHER-1.",
                      "Edit issue → set Project Parent to NPIOTHER-1 if this is a non-PEP NPI.")
            else:
                # Container is inside a proper PEP project (e.g. DDE5122) — that's fine
                r.add(Severity.INFO, cat,
                      f"Container is in project '{project_key}' — assumed to be a valid PEP/Transfer project.")

    def _check_summary_nomenclature(self, r: AuditReport, summary: str):
        cat = "Summary / Naming"
        if not summary.strip():
            r.add(Severity.ERROR, cat, "Summary is empty.",
                  "Required format: [DS|QS|PT|PR|DMR] DDE-XXXX PTxx-XXXX <description>")
            return

        # Pattern: BuildType DDE-number PTxx-number description
        # e.g. "DS DDE-4800 PTDE-4711 PCBA #7012345 Top test of new IC"
        pattern = r'^(DS|QS|PT|PR|DMR)\s+(DDE-\d+|PTDE-\d+|PTSG-\d+|DDE-[A-Z0-9]+)\s+PT[A-Z]{2}-\d+\s+.+'
        loose   = r'^(DS|QS|PT|PR|DMR)\s+'

        if re.match(pattern, summary, re.IGNORECASE):
            r.add(Severity.OK, cat, f"Summary follows naming convention: '{summary}'")
        elif re.match(loose, summary, re.IGNORECASE):
            # If container is already inside a PEP project, DDE/PTxx in summary is optional
            r.add(Severity.INFO, cat,
                  f"Summary starts with valid build type: '{summary}'. "
                  "DDE/PTxx numbers optional if container is inside a PEP project.")
        else:
            r.add(Severity.ERROR, cat,
                  f"Summary doesn't follow the required naming convention: '{summary}'",
                  "Must start with DS/QS/PT/PR/DMR, followed by DDE number, PTxx doc number, then description.")

        # Check for generic/placeholder names
        if re.search(r'\btest\b|\bxxx\b|\bsample\b', summary, re.IGNORECASE) and len(summary) < 30:
            r.add(Severity.WARNING, cat,
                  "Summary may be a placeholder or test name — confirm it's meaningful.",
                  "Summary must be useful for back-end data handling and KanBan visibility.")

    def _check_status_vs_content(self, r: AuditReport, status: str, description: str):
        cat = "Status Consistency"
        has_desc = bool(description and len(description.strip()) > 100)

        if status == "Waiting" and not has_desc:
            r.add(Severity.ERROR, cat,
                  "Container is in 'Waiting' (requested) status but description appears empty or very short.",
                  "NPI template must be deployed and filled before requesting. "
                  "The Jira 'Request' transition should auto-populate description from ITPL-1474.")
        elif status == "Backlog" and has_desc:
            r.add(Severity.WARNING, cat,
                  "Container is in 'Backlog' but description is already populated — requestor may have "
                  "clicked 'Deploy Template' instead of 'Request', or the Request transition failed silently.",
                  "Ask requestor to open the container, check the workflow dropdown, and confirm 'Request' "
                  "was clicked (not 'Deploy Template'). Status must move to 'Waiting' after a successful request. "
                  "If Order Type / Product Type / NPI Location were blank during request, the Jira script "
                  "would have failed — check those fields first.")
        elif status in ("In Progress", "Waiting") and has_desc:
            r.add(Severity.OK, cat, f"Status '{status}' with populated description looks correct.")
        elif status == "Done":
            r.add(Severity.INFO, cat, "Container is closed (Done). Audit is informational only.")

    def _check_core_fields(self, r: AuditReport, order_type, product_type, npi_location, project_number):
        cat = "Core Fields (Request Form)"

        # Order Type — full JIRA value is e.g. "DS – Development sample"
        if not order_type:
            r.add(Severity.ERROR, cat,
                  "Order Type not set (DS/QS/PT/PR/DMR).",
                  "Select Order Type when clicking 'Request' → 'NPI Request'.")
        else:
            prefix = order_type.split("–")[0].split("-")[0].strip().upper()
            if prefix in ORDER_TYPES:
                r.add(Severity.OK, cat, f"Order Type set: {order_type}")
            else:
                r.add(Severity.WARNING, cat,
                      f"Order Type value '{order_type}' is unexpected — expected one of {ORDER_TYPES}.")
        # Product Type
        if not product_type:
            r.add(Severity.ERROR, cat,
                  "Product Type not set (SMT PCBA / Subassembly / Final Good).",
                  "Set Product Type during the NPI Request step.")
        else:
            r.add(Severity.OK, cat, f"Product Type set: {product_type}")

        # NPI Location
        if not npi_location:
            r.add(Severity.ERROR, cat,
                  "NPI Location not set (e.g. CZ2, MF1, SGP).",
                  "Specify where the NPI will be built in the Request form.")
        else:
            r.add(Severity.OK, cat, f"NPI Location set: {npi_location}")

        # Project Number
        if not project_number:
            r.add(Severity.WARNING, cat,
                  "Project Number (DDE/VE No.) not set.",
                  "Fill in the Project Number field in the Request form for proper BigPicture linking.")
        else:
            r.add(Severity.OK, cat, f"Project Number set: {project_number}")

    def _check_ptx_for_build_type(self, r: AuditReport, order_type, ptx_document, summary: str):
        cat = "EDM / PTxx Document"
        build_type = (order_type or self._extract_build_type_from_summary(summary) or "").upper()

        requires_ptx = build_type in {"QS", "PT", "PR"}

        if requires_ptx:
            if not ptx_document:
                r.add(Severity.ERROR, cat,
                      f"Build type '{build_type}' requires a PTxx EDM document but none is linked.",
                      "Create PTxx entry in EDM first, then link it in the NPI Request form. "
                      "After requesting, print WC to PDF and upload/release in EDM-PTxx.")
            else:
                r.add(Severity.OK, cat, f"PTxx Document linked: {ptx_document}")
        else:
            if build_type == "DS":
                r.add(Severity.INFO, cat, "DS build type — PTxx EDM document not required.")
            else:
                r.add(Severity.INFO, cat, "PTxx EDM document check: build type not confirmed (check manually).")

    def _check_assignee(self, r: AuditReport, assignee, product_type, status: str):
        cat = "Assignee"
        if status in ("Backlog",):
            r.add(Severity.INFO, cat, "Container not yet requested — assignee check skipped.")
            return

        pt = (product_type or "").lower()
        is_smt = "smt" in pt or "pcba" in pt

        if is_smt:
            if assignee is None:
                r.add(Severity.OK, cat, "SMT PCBA — Assignee is unassigned (correct per procedure).")
            else:
                name = assignee.get("displayName", "someone")
                r.add(Severity.WARNING, cat,
                      f"SMT PCBA NPI should be 'Unassigned' but is assigned to {name}.",
                      "SMT NPI work containers should be left unassigned; ExpressOPS lead assigns internally.")
        else:
            # FG / Subassembly — must have an assignee
            if assignee is None:
                r.add(Severity.ERROR, cat,
                      "FG/Subassembly NPI requires an assigned OPS responsible (VSE/SCPL/M+S) but is unassigned.",
                      "Requestor must assign the actual OPS NPI built responsible for FG/Subassembly types.")
            else:
                name = assignee.get("displayName", "someone")
                r.add(Severity.OK, cat, f"FG/Subassembly NPI assigned to: {name}")

    def _check_description_template(self, r: AuditReport, description: str, order_type, status: str):
        cat = "Description / Template"

        if not description or len(description.strip()) < 50:
            if status in ("Waiting", "In Progress"):
                r.add(Severity.ERROR, cat,
                      "Description is empty or too short — NPI template (ITPL-1474) was not deployed.",
                      "The template is auto-deployed when you click 'Deploy Template' then 'Request'. "
                      "If missing, the NPI Request step may have been skipped or done incorrectly.")
            else:
                r.add(Severity.INFO, cat, "Description empty — NPI not yet requested. OK for Backlog.")
            return

        # Count how many times the template header appears — more than once = duplicated
        npi_overview_count = description.lower().count("npi overview")
        if npi_overview_count > 1:
            r.add(Severity.ERROR, cat,
                  f"NPI template appears {npi_overview_count} times in description — template was deployed multiple times.",
                  "Requestor must edit the description, delete all duplicate template copies, "
                  "and keep only one. Use 'Visual' mode in Jira to find and remove the duplicates. "
                  "This happens when 'Deploy Template' is clicked more than once.")
        else:
            r.add(Severity.OK, cat, "Description has content (template appears deployed once).")

        # Check required sections exist
        for section in REQUIRED_SECTIONS:
            if self._desc_contains(description, section):
                r.add(Severity.OK, cat, f"Section found: '{section}'")
            else:
                r.add(Severity.WARNING, cat,
                      f"Section '{section}' not found in description.",
                      f"Check if template section '{section}' was accidentally deleted or not filled.")

        # Check for unfilled placeholders
        found_placeholders = []
        for pat in PLACEHOLDER_PATTERNS:
            if re.search(pat, description, re.IGNORECASE):
                found_placeholders.append(pat.replace(r"\b", "").replace("\\", ""))

        if found_placeholders:
            r.add(Severity.WARNING, cat,
                  f"Description may contain unfilled template placeholders: {found_placeholders}",
                  "Review description in 'Visual' mode and replace all placeholder values (#xxxxx, ##, etc.).")
        else:
            r.add(Severity.OK, cat, "No obvious unfilled placeholders detected.")

        # PR/DMR-specific: FOQ qty and yearly forecast
        build_type = (order_type or self._extract_build_type_from_summary("") or "").upper()
        if build_type in {"PR", "DMR"}:
            if not self._desc_contains(description, "FOQ") or not self._desc_contains(description, "Forecast"):
                r.add(Severity.ERROR, cat,
                      "PR/DMR build type requires FOQ Qty and Yearly Forecast Qty in the Built Type table.",
                      "Fill in FOQ Qty, Yearly Forecast Qty, and expected FOQ completion date in the NPI Built Type & Quantities table.")

        # Focus Material — if it still has pre-filled content and requestor didn't clear it
        if self._desc_contains(description, "Focus Material"):
            if re.search(r'#\s*PCB|SMT\s+compone', description, re.IGNORECASE):
                r.add(Severity.WARNING, cat,
                      "Focus Material table may still have pre-filled placeholder rows (PCB / SMT component rows).",
                      "Either fill in actual material details, or delete the pre-filled rows if no special material applies.")

        # Dual Use section — check it's actually filled
        if self._desc_contains(description, "Dual Use"):
            if re.search(r'fill out only|Part number.*Name.*–\s*–', description, re.IGNORECASE):
                r.add(Severity.INFO, cat,
                      "Dual Use table appears to have only placeholder rows (–, –). "
                      "Confirm Dual Use check was run in M3 for this PCBA.")

    def _check_bom_release_hint(self, r: AuditReport, fields, description: str, status: str):
        cat = "BOM / Material Readiness"
        # We can't directly check M3 BOM release from JIRA, but we can check
        # if the description mentions BOM or material status (requestor should have noted it)
        if status not in ("Waiting", "In Progress"):
            return

        if self._desc_contains(description, "BOM") or self._desc_contains(description, "R&D 1") or self._desc_contains(description, "XECX450"):
            r.add(Severity.OK, cat, "Description references BOM/release — verify actual release status in M3 (XECX450, R&D 1 & R&D 2).")
        else:
            r.add(Severity.WARNING, cat,
                  "No BOM release reference found in description.",
                  "Before requesting, BOM must be released in M3 (R&D 1 and R&D 2 via #E5: XECX450). "
                  "Confirm this was done, or ask requestor to confirm in a Jira comment.")

        # Long-lead material mention
        if not self._desc_contains(description, "long lead") and not self._desc_contains(description, "long-lead"):
            r.add(Severity.INFO, cat,
                  "No mention of long lead material. If any exists, it should be noted in Focus Material table.")

    def _check_parking(self, r: AuditReport, parking_log, status: str):
        cat = "Parking"
        if not parking_log:
            r.add(Severity.OK, cat, "No parking log — container has not been parked.")
            return
        # Parking log format: "Start:YYYY-MM-DD HH:MM:SS;End:YYYY-MM-DD HH:MM:SS;"
        # An open parking entry has Start with no End
        open_parks = re.findall(r'Start:([^;]+);(?!End:)', parking_log)
        if open_parks and status in ("In Progress", "Waiting"):
            r.add(Severity.WARNING, cat,
                  f"Container is currently PARKED (started: {open_parks[0].strip()}) but status is '{status}'.",
                  "If parking is resolved, remove the Parked flag in the WC edit view so KPI run time resumes.")
        elif parking_log:
            r.add(Severity.INFO, cat, f"Container has parking history: {parking_log[:80]}...")

    def _check_request_type(self, r: AuditReport, request_type, status: str):
        cat = "Request Type"
        if status == "Backlog":
            r.add(Severity.INFO, cat, "Container in Backlog — Request Type not yet set. OK.")
            return
        if not request_type:
            r.add(Severity.ERROR, cat,
                  "Request Type not set — NPI was not formally requested via the 'Request' workflow.",
                  "Requestor must click the 'Request' button, select 'NPI Request' under Request Type, "
                  "and fill the request form. This auto-deploys the template and moves status to Waiting.")
        elif "NPI Request" in request_type or "NPI Template" in request_type:
            r.add(Severity.OK, cat, f"Request Type: {request_type}")
        else:
            r.add(Severity.WARNING, cat,
                  f"Unexpected Request Type: '{request_type}'. Expected 'NPI Request'.",
                  "Check whether the correct request type was selected during the Request workflow.")

    def _check_wc_status_field(self, r: AuditReport, npi_wc_status):
        cat = "NPI WC Status"
        if not npi_wc_status:
            r.add(Severity.INFO, cat, "NPI WC Status field not found or not set. Will default to green when processed.")
        elif npi_wc_status.lower() == "red":
            r.add(Severity.WARNING, cat,
                  "NPI WC Status is RED — container is flagged as delayed.",
                  "Check which work package caused the delay. Update due dates or park the NPI if the delay is external.")
        elif npi_wc_status.lower() == "green":
            r.add(Severity.OK, cat, "NPI WC Status is GREEN.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Audit a P+F JIRA NPI WorkContainer against the NPI Toolchain procedure."
    )
    parser.add_argument("issue_key",
                        help="JIRA issue key, e.g. NPIOTHER-123 or PTDE-456")
    parser.add_argument("--json",   action="store_true",
                        help="Output results as JSON instead of human-readable.")
    parser.add_argument("--no-ok",  action="store_true",
                        help="Suppress passed checks — show only errors/warnings.")
    parser.add_argument("--no-fix", action="store_true",
                        help="Suppress fix hints.")
    parser.add_argument("--fields", action="store_true",
                        help="Print all custom field IDs from your JIRA instance (for config).")
    args = parser.parse_args()

    Config.load_file()

    if not Config.JIRA_PAT:
        print("⚠️  JIRA PAT not set.")
        print("   Option 1: set environment variable JIRA_PAT=<your_token>")
        print("   Option 2: ensure config.yaml has a 'pat:' key under the jira section")
        print("   Option 3: create config.json with key JIRA_PAT")
        sys.exit(1)

    client  = JiraClient(Config.JIRA_BASE_URL, Config.JIRA_PAT)
    auditor = NPIAuditor(client)

    if args.fields:
        print("Discovering custom fields in your JIRA instance...")
        cf = client.discover_custom_fields()
        for fid, name in sorted(cf.items(), key=lambda x: x[1]):
            print(f"  {fid:30s}  {name}")
        return

    try:
        report = auditor.audit(args.issue_key)
    except PermissionError as e:
        print(f"Auth error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        report.print(show_ok=not args.no_ok, show_fix=not args.no_fix)


# ---------------------------------------------------------------------------
# Batch mode — audit multiple containers at once
# ---------------------------------------------------------------------------

def audit_batch(issue_keys: List[str]) -> List[AuditReport]:
    """
    Audit multiple containers. Returns list of AuditReport objects.
    Usage from another script:
        from npi_auditor import audit_batch, Config
        Config.JIRA_PAT = "your_pat_token"
        reports = audit_batch(["NPIOTHER-100", "NPIOTHER-101", "PTDE-200"])
        for r in reports:
            r.print(show_ok=False)
    """
    Config.load_file()
    client  = JiraClient(Config.JIRA_BASE_URL, Config.JIRA_PAT)
    auditor = NPIAuditor(client)
    results = []
    for key in issue_keys:
        try:
            results.append(auditor.audit(key))
        except Exception as e:
            print(f"Could not audit {key}: {e}")
    return results


if __name__ == "__main__":
    main()
