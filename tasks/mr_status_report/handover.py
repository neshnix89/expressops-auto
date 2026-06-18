#!/usr/bin/env python3
"""
PE / TE Handover workflow lookup for the MR Status Report.
=========================================================
The "Handover PE" and "Handover TE" columns are populated from two Confluence
page trees whose approval state is managed by the **Comala Document Management**
plugin ("Simple Approval Workflow"). The state is NOT in the page body — it is
read via:

    GET /rest/cw/1/content/{contentId}/status   ->  state.name in
        {"Approved" (final), "Review"/... (pending)}

Page structure (confirmed by probe, 2026-06-18):
  * PE parent 572625450 — weekly child pages "WKxx/2026 IE PE to MX PE handover".
      - newer weeks: one SUB-PAGE per PT, PT in the title (e.g. "PTDE-AY40_X7300"),
        each with its own workflow.
      - older weeks: the workflow is on the weekly page itself, PTs listed in a
        table inside (Transfer Request column).
  * TE parent 572625454 — one child page PER PT, PT in the title
      (e.g. "Wk23/26: PTDE-AY55 PCBA TE to MX Handover"), each with its workflow.

So the rule is uniform: find the page whose title carries the container's PT
number, read that page's workflow state. A fallback parses the old weekly-table
pages so PTs that only appear in a table still resolve.

All calls here are GET (read-only).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("MR_Report")

PE_PARENT = "572625450"
TE_PARENT = "572625454"

# PT number as it appears in a page title or body, e.g. PTDE-AY55, PTDE-AXD9A,
# PTCZ-1637, PTCZ-1635A. (Two letters, dash, then alphanumerics inc. a revision.)
PT_RE = re.compile(r'PT[A-Z]{2}-[A-Z0-9]+', re.IGNORECASE)

_MAX_DEPTH = 4


def _norm(pt: str) -> str:
    """Normalise a PT number for matching: uppercase, no spaces."""
    return re.sub(r'\s+', '', str(pt or '')).upper()


def _base(pt: str) -> str:
    """Revision-insensitive key: drop trailing letters that follow a digit.

    PTDE-AXD9A -> PTDE-AXD9 ;  PTDE-AY55 -> PTDE-AY55 (unchanged).
    Lets a container PT match a page PT that differs only by an A/B revision.
    """
    return re.sub(r'(\d)[A-Z]+$', r'\1', _norm(pt))


def _children(csess, base_url: str, cid: str) -> list[dict]:
    """All immediate child pages of a content id (paginated)."""
    out: list[dict] = []
    start = 0
    while True:
        url = f"{base_url}/rest/api/content/{cid}/child/page?limit=100&start={start}"
        try:
            r = csess.get(url, timeout=25)
        except Exception as e:
            log.warning(f"  handover: child fetch error for {cid}: {e}")
            break
        if r.status_code != 200:
            break
        results = r.json().get("results", [])
        out.extend(results)
        if len(results) < 100:
            break
        start += 100
    return out


def _descendants(csess, base_url: str, parent: str) -> list[dict]:
    """Breadth-first collect of all descendant pages (id + title)."""
    found: list[dict] = []
    frontier = [(parent, 0)]
    seen = {parent}
    while frontier:
        cid, depth = frontier.pop()
        if depth >= _MAX_DEPTH:
            continue
        for c in _children(csess, base_url, cid):
            kid = c.get("id")
            if not kid or kid in seen:
                continue
            seen.add(kid)
            found.append({"id": kid, "title": c.get("title", "")})
            frontier.append((kid, depth + 1))
    return found


def _state(csess, base_url: str, cid: str) -> str | None:
    """Comala workflow state name for a page, or None if it has no workflow."""
    try:
        r = csess.get(f"{base_url}/rest/cw/1/content/{cid}/status", timeout=20)
    except Exception as e:
        log.debug(f"  handover: state error for {cid}: {e}")
        return None
    if r.status_code != 200:
        return None  # 204 = no workflow on this page
    try:
        return r.json().get("state", {}).get("name")
    except Exception:
        return None


def _storage(csess, base_url: str, cid: str) -> str:
    """Storage-format body of a page (for the old weekly-table fallback)."""
    try:
        r = csess.get(
            f"{base_url}/rest/api/content/{cid}?expand=body.storage", timeout=25
        )
        if r.status_code == 200:
            return r.json().get("body", {}).get("storage", {}).get("value", "") or ""
    except Exception as e:
        log.debug(f"  handover: storage error for {cid}: {e}")
    return ""


def _build_map(csess, base_url: str, parent: str, label: str) -> dict[str, str]:
    """Build {normalised PT -> workflow state name} for a handover tree."""
    pages = _descendants(csess, base_url, parent)
    if not pages:
        log.warning(f"  handover[{label}]: no descendant pages under {parent}")
        return {}

    # Fetch every page's workflow state in parallel.
    with ThreadPoolExecutor(max_workers=10) as ex:
        states = list(ex.map(lambda p: _state(csess, base_url, p["id"]), pages))
    for p, st in zip(pages, states):
        p["state"] = st

    pt_map: dict[str, str] = {}

    # Pass 1: per-PT pages — PT in the title, page has its own workflow.
    for p in pages:
        st = p["state"]
        title = p["title"]
        if not st:
            continue
        if "template" in title.lower():
            continue
        m = PT_RE.search(title)
        if m:
            pt_map[_norm(m.group(0))] = st

    # Pass 2: old weekly-table pages — page has a workflow but no PT in its
    # title; the PTs live in the table body. Don't override per-PT pages.
    for p in pages:
        st = p["state"]
        title = p["title"]
        if not st or PT_RE.search(title):
            continue
        body = _storage(csess, base_url, p["id"])
        for pt in set(PT_RE.findall(body)):
            pt_map.setdefault(_norm(pt), st)

    log.info(f"  handover[{label}]: {len(pt_map)} PT->state entries "
             f"from {len(pages)} pages")
    return pt_map


def fetch_handover_states(csess, base_url: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return (pe_map, te_map): normalised PT number -> Comala state name."""
    pe_map = _build_map(csess, base_url, PE_PARENT, "PE")
    te_map = _build_map(csess, base_url, TE_PARENT, "TE")
    return pe_map, te_map


def _lookup(pt: str, mapping: dict[str, str]) -> str | None:
    """Find a PT's state: exact match first, then revision-insensitive."""
    if not pt or not mapping:
        return None
    n = _norm(pt)
    if n in mapping:
        return mapping[n]
    b = _base(n)
    cands = [v for k, v in mapping.items() if _base(k) == b]
    if cands:
        # Prefer Approved if any revision is approved.
        return "Approved" if any(str(c).strip().lower() == "approved" for c in cands) else cands[0]
    return None


def handover_status(pt: str, mapping: dict[str, str]) -> str:
    """Display value for a Handover cell: 'Approved' / 'Pending' / 'No handover'."""
    st = _lookup(pt, mapping)
    if st is None:
        return "No handover"
    return "Approved" if str(st).strip().lower() == "approved" else "Pending"
