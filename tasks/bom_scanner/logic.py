"""
Pure business logic for bom_scanner.

No I/O here — functions take plain dicts/strings and return results.
Keeps logic unit-testable without JIRA / Confluence / M3 access.

The BOM Scanner:
  A) gathers active Work Containers from JIRA and Confluence
  B) extracts one-or-more article numbers from each container's Description
  C) queries M3 PDS (MPDMAT + MITMAS_AP) for any component whose PLC != 310
  D) posts a [~reporter] JIRA comment on containers with flagged components
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


# ── Article number extraction ────────────────────────────────────────

# Patterns are intentionally redundant — the NPI Description is free-form
# wiki markup, and real-world containers include article numbers as
# #70203371, Y70184012, bare 70xxxxxx, or after PCB/PCBA labels.
_ARTICLE_PATTERNS = (
    re.compile(r"#(\d{6,8})"),
    re.compile(r"Y(\d{7,8})"),
    re.compile(r"\b(70\d{5,6})\b"),
    re.compile(r"(?:PCB|PCBA)\s*#?\s*(\d{6,8})", re.IGNORECASE),
)


def extract_article_numbers(text: str) -> list[str]:
    """Return every unique article number mentioned in a description body."""
    if not text:
        return []
    found: list[str] = []
    for pattern in _ARTICLE_PATTERNS:
        for match in pattern.finditer(text):
            found.append(match.group(1))
    # preserve insertion order while deduping
    seen: set[str] = set()
    unique: list[str] = []
    for art in found:
        if art not in seen:
            seen.add(art)
            unique.append(art)
    return unique


# ── Confluence container-key extraction ──────────────────────────────

def extract_confluence_container_keys(html: str) -> set[str]:
    """
    Pull JIRA container keys from the MR Status Report Confluence page.

    The page has three tables: MR Week Schedule, Active MR, and
    COMPLETED MR. We scan only the first two — completed containers
    are out of scope for BOM checks.

    Container keys live inside <a href=".../browse/KEY"> links.
    """
    if not html:
        return set()
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    keys: set[str] = set()
    for table in tables[:2]:  # skip COMPLETED MR (table index 2)
        for a in table.find_all("a", href=True):
            href = a["href"]
            if "/browse/" in href:
                key = href.split("/browse/")[-1].strip()
                # href can carry query strings — take the bare key
                key = key.split("?")[0].split("#")[0]
                if key:
                    keys.add(key)
    return keys


# ── Source deduplication ─────────────────────────────────────────────

def dedupe_containers(
    jira_keys: list[str],
    confluence_keys: set[str],
) -> list[dict[str, Any]]:
    """
    Merge container keys from both sources into a single ordered list.

    Each record is `{"key": ..., "sources": ["jira", "confluence", ...]}`
    with sources in deterministic order.
    """
    order: list[str] = []
    by_key: dict[str, set[str]] = {}

    for key in jira_keys:
        if key not in by_key:
            by_key[key] = set()
            order.append(key)
        by_key[key].add("jira")

    for key in confluence_keys:
        if key not in by_key:
            by_key[key] = set()
            order.append(key)
        by_key[key].add("confluence")

    return [
        {"key": key, "sources": sorted(by_key[key])}
        for key in order
    ]


# ── Flag rule ────────────────────────────────────────────────────────

def should_flag(components: list[dict[str, Any]]) -> bool:
    """True when any returned component has a PLC other than the target."""
    return bool(components)


def normalize_component(row: dict[str, Any]) -> dict[str, str]:
    """
    Collapse an M3 MPDMAT+MITMAS_AP row to the fields the scanner uses.

    The ODBC driver returns uppercase column keys. Values may be padded
    strings — strip them so the comment body and Confluence cells read
    cleanly.
    """
    def _s(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    return {
        "component": _s(row.get("PMMTNO")),
        "plc": _s(row.get("MMCFI3")),
        "status": _s(row.get("MMSTAT")),
        "description": _s(row.get("MMITDS")),
    }


# ── Comment body ─────────────────────────────────────────────────────

BOM_SCANNER_MARKER = "(Automated by BOM Scanner)"


def build_aggregated_comment_body(
    reporter_name: str,
    articles_with_flags: list[dict[str, Any]],
    target_status: str,
) -> str:
    """
    JIRA wiki-markup comment body covering every flagged article in one
    container — one comment total, regardless of how many articles.

    Each entry in ``articles_with_flags`` is the same dict the scanner
    records in ``result["articles"]`` — at minimum ``article_number`` and
    ``flagged`` (list of normalized component dicts). Entries with an
    empty ``flagged`` list are ignored; the caller is expected to have
    filtered already, but we double-check to avoid emitting an empty
    section.

    The italicised marker line is still a plain substring, so
    :func:`already_commented` continues to match it on re-runs.
    """
    header = (
        f"[~{reporter_name}] BOM PLC Check \u2014 the following components "
        f"have PLC status != {target_status}:"
    )

    sections: list[str] = []
    for art in articles_with_flags:
        flagged = art.get("flagged") or []
        if not flagged:
            continue
        article_number = art.get("article_number", "")
        rows = ["|| Component || PLC || Description ||"]
        for comp in flagged:
            component = comp.get("component", "")
            plc = comp.get("plc", "") or "(blank)"
            description = comp.get("description", "")
            rows.append(f"| {component} | {plc} | {description} |")
        sections.append(f"*Article {article_number}:*\n" + "\n".join(rows))

    body_sections = "\n\n".join(sections)
    footer = (
        f"Please update the PLC status to {target_status} before proceeding "
        f"with MR.\n_{BOM_SCANNER_MARKER}_"
    )
    return f"{header}\n\n{body_sections}\n\n{footer}"


def already_commented(comments: list[dict[str, Any]]) -> bool:
    """True if any existing comment carries the BOM Scanner marker line."""
    for c in comments or []:
        body = c.get("body") or ""
        if BOM_SCANNER_MARKER in body:
            return True
    return False


# ── Result rows for Confluence publish ───────────────────────────────

def build_confluence_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Flatten scanner results into one row per (container, article) pair.

    Containers with no extractable article numbers yield a single row
    carrying a `-` article and `no article #` outcome. Containers with
    multiple articles yield one row per article so the Confluence reader
    can see which article drove a flag.
    """
    rows: list[dict[str, Any]] = []
    for r in results:
        key = r["key"]
        sources = ", ".join(r.get("sources") or [])
        reporter = r.get("reporter") or ""
        articles = r.get("articles") or []
        action = r.get("action_taken") or ""

        if not articles:
            rows.append({
                "key": key,
                "sources": sources,
                "article": "-",
                "flagged_count": 0,
                "component_details": "",
                "reporter": reporter,
                "action_taken": action or "no article #",
            })
            continue

        for art in articles:
            flagged = art.get("flagged") or []
            details = "; ".join(
                f"{c['component']} (PLC {c['plc'] or 'blank'})"
                for c in flagged
            )
            rows.append({
                "key": key,
                "sources": sources,
                "article": art.get("article_number", ""),
                "flagged_count": len(flagged),
                "component_details": details or art.get("note", ""),
                "reporter": reporter,
                "action_taken": action,
            })
    return rows


# ── Console summary ──────────────────────────────────────────────────

def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    total = len(results)
    with_articles = sum(1 for r in results if r.get("articles"))
    flagged = sum(
        1 for r in results
        if any(a.get("flagged") for a in (r.get("articles") or []))
    )
    commented = sum(1 for r in results if r.get("action_taken") == "comment posted")
    return {
        "total": total,
        "with_articles": with_articles,
        "no_articles": total - with_articles,
        "flagged_containers": flagged,
        "commented": commented,
    }


def format_table(results: list[dict[str, Any]]) -> str:
    rows = build_confluence_rows(results)
    if not rows:
        return "(no containers)"

    header = (
        "Container", "Source", "Article", "Flagged", "Reporter", "Action",
    )
    data = [
        (
            r["key"],
            r["sources"],
            str(r["article"]),
            str(r["flagged_count"]),
            r["reporter"] or "-",
            r["action_taken"] or "-",
        )
        for r in rows
    ]
    widths = [
        max(len(header[i]), max((len(row[i]) for row in data), default=0))
        for i in range(len(header))
    ]

    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt(header), fmt(tuple("-" * w for w in widths))]
    for row in data:
        lines.append(fmt(row))
    return "\n".join(lines)
