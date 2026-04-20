"""
Publish bom_scanner results to Confluence.

The page is structured so the operator can focus on what matters:

    1. Summary banner        — scan timestamp, totals, target status
    2. Primary Articles (SPI) — flagged components, colour-coded by PLC
    3. Reference Articles (SNO) — same shape, kept separate
    4. Clean Containers       — collapsed expand macro, minimal info
    5. Skipped Containers     — collapsed expand macro, skip reason
    6. Footer                 — code macros for re-run + comment commands

Auth pattern is unchanged: Bearer PAT to pfteamspace.pepperl-fuchs.com
with the target page pulled from ``config.pages['bom_scanner']``.
"""

from __future__ import annotations

import html
from datetime import datetime
from itertools import groupby
from typing import Any

from core.config_loader import Config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError
from core.logger import get_logger

logger = get_logger("bom_scanner.publish")

PAGE_KEY = "bom_scanner"

RUN_BAT_PATH = r"C:\Users\tmoghanan\Documents\AI\expressops-auto\scripts\bom_scanner_run.bat"
RUN_PS_CMD = (
    r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe "
    r"-m tasks.bom_scanner.main scan --live --target-status 310"
)
COMMENT_PS_CMD = (
    r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe "
    r"-m tasks.bom_scanner.main comment --live --target-status 310 "
    r"--keys KEY1 KEY2"
)


# ── Row / badge helpers ──────────────────────────────────────────────

def _esc(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def _code_macro(language: str, body: str) -> str:
    return (
        '<ac:structured-macro ac:name="code">'
        f'<ac:parameter ac:name="language">{language}</ac:parameter>'
        f'<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>'
        '</ac:structured-macro>'
    )


def _expand_macro(title: str, body_html: str) -> str:
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
        f'<ac:rich-text-body>{body_html}</ac:rich-text-body>'
        '</ac:structured-macro>'
    )


def _status_macro(colour: str, title: str) -> str:
    """Confluence status macro — renders as a small coloured badge."""
    return (
        '<ac:structured-macro ac:name="status">'
        f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
        '</ac:structured-macro>'
    )


def _source_badge(sources: list[str]) -> str:
    """JIRA / CONFLUENCE / BOTH status macro based on where the key was found."""
    s = set(sources or [])
    if s == {"jira"}:
        return _status_macro("Blue", "JIRA")
    if s == {"confluence"}:
        return _status_macro("Purple", "CONFLUENCE")
    if {"jira", "confluence"}.issubset(s):
        return _status_macro("Green", "BOTH")
    # anything else (e.g. the "manual" sentinel from the comment command)
    return _status_macro("Grey", (", ".join(sorted(s)) or "NONE").upper())


def _order_type_badge(order_type: str | None) -> str:
    """Bold coloured pill for SPI (primary) / SNO (reference)."""
    ot = (order_type or "").upper()
    if ot == "SPI":
        return (
            '<span style="background-color:#27AE60;color:#FFFFFF;'
            'padding:2px 8px;border-radius:3px;font-weight:bold;">SPI</span>'
        )
    if ot == "SNO":
        return (
            '<span style="background-color:#2980B9;color:#FFFFFF;'
            'padding:2px 8px;border-radius:3px;">SNO</span>'
        )
    return _esc(order_type or "-")


def _plc_style(plc: str) -> tuple[str, str]:
    """Return (background, foreground) hex for a PLC status cell."""
    p = (plc or "").strip().upper()
    if not p:
        return ("#2C3E50", "#FFFFFF")
    if p == "NEW":
        return ("#F39C12", "#FFFFFF")
    if p == "INT":
        return ("#F1C40F", "#222222")
    if p.isdigit():
        n = int(p)
        if 200 <= n <= 299:
            return ("#E74C3C", "#FFFFFF")
        if 300 <= n <= 309:
            return ("#3498DB", "#FFFFFF")
        if 311 <= n <= 399:
            return ("#3498DB", "#FFFFFF")
        if 400 <= n <= 499:
            return ("#8E44AD", "#FFFFFF")
        if 500 <= n <= 599:
            return ("#C0392B", "#FFFFFF")
        if 600 <= n <= 699:
            return ("#7F8C8D", "#FFFFFF")
    return ("#95A5A6", "#222222")


def _plc_cell(plc: str) -> str:
    bg, fg = _plc_style(plc)
    label = (plc or "").strip() or "(blank)"
    return (
        f'<td style="background-color:{bg};color:{fg};'
        f'text-align:center;font-weight:bold;">'
        f"{html.escape(label)}</td>"
    )


def _plc_sort_key(plc: str) -> tuple:
    p = (plc or "").strip().upper()
    if not p:
        return (4, 0, "")  # blank last
    if p == "NEW":
        return (0, 0, p)
    if p == "INT":
        return (0, 1, p)
    if p.isdigit():
        return (1, int(p), p)
    return (2, 0, p)


def _container_link(base_url: str, key: str) -> str:
    href = f"{base_url.rstrip('/')}/browse/{key}"
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(key)}</a>'


# ── Data shaping ─────────────────────────────────────────────────────

def _classify_results(results: list[dict[str, Any]]) -> dict[str, list]:
    """
    Flatten results into four buckets used by the Confluence layout.

    `spi_rows` / `sno_rows` are per-component rows for the respective
    section tables. `clean` and `skipped` are per-container records for
    the collapsed sections.
    """
    spi_rows: list[dict[str, Any]] = []
    sno_rows: list[dict[str, Any]] = []
    clean: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for r in results:
        key = r.get("key", "?")
        sources = r.get("sources") or []
        articles = r.get("articles") or []

        has_flag_spi = False
        has_flag_sno = False
        has_any_valid_article = False

        for art in articles:
            order_type = (art.get("order_type") or "").upper()
            if order_type in ("SPI", "SNO"):
                has_any_valid_article = True
            if not art.get("flagged"):
                continue
            for comp in art["flagged"]:
                row = {
                    "key": key,
                    "sources": sources,
                    "article": art.get("article_number", ""),
                    "order_type": order_type,
                    "component": comp.get("component", ""),
                    "plc": comp.get("plc", ""),
                    "description": comp.get("description", ""),
                }
                if order_type == "SPI":
                    spi_rows.append(row)
                    has_flag_spi = True
                elif order_type == "SNO":
                    sno_rows.append(row)
                    has_flag_sno = True

        if has_flag_spi or has_flag_sno:
            continue

        if has_any_valid_article:
            clean_articles = [
                {
                    "article_number": a.get("article_number", ""),
                    "order_type": (a.get("order_type") or "").upper(),
                }
                for a in articles
                if (a.get("order_type") or "").upper() in ("SPI", "SNO")
            ]
            clean.append({
                "key": key,
                "sources": sources,
                "articles": clean_articles,
            })
        else:
            reasons = []
            if not articles:
                reasons.append("no article #")
            else:
                for a in articles:
                    note = a.get("note") or "skipped"
                    reasons.append(f"{a.get('article_number','?')}: {note}")
            skipped.append({
                "key": key,
                "sources": sources,
                "reasons": reasons,
            })

    return {
        "spi_rows": spi_rows,
        "sno_rows": sno_rows,
        "clean": clean,
        "skipped": skipped,
    }


# ── Section renderers ────────────────────────────────────────────────

_FLAG_TABLE_HEADERS = (
    "Container", "Source", "Article #", "Order Type",
    "Component", "PLC Status", "Description",
)


def _render_flag_table(
    base_url: str,
    rows: list[dict[str, Any]],
) -> str:
    """
    Build the SPI or SNO flagged-components table. Rows are sorted by
    PLC (NEW → INT → numeric ascending → other → blank). Whenever the
    PLC changes, a full-width coloured sub-header is emitted announcing
    the group and its size.
    """
    if not rows:
        return "<p><em>No flagged components in this section.</em></p>"

    sorted_rows = sorted(rows, key=lambda r: _plc_sort_key(r["plc"]))
    header_html = "".join(f"<th>{html.escape(h)}</th>" for h in _FLAG_TABLE_HEADERS)

    body_parts: list[str] = [f"<tr>{header_html}</tr>"]
    for plc_value, group_iter in groupby(sorted_rows, key=lambda r: (r["plc"] or "").strip()):
        group = list(group_iter)
        bg, fg = _plc_style(plc_value)
        label = plc_value or "(blank)"
        body_parts.append(
            f'<tr><td colspan="{len(_FLAG_TABLE_HEADERS)}" '
            f'style="background-color:{bg};color:{fg};'
            f'text-align:left;font-weight:bold;padding:6px;">'
            f"PLC: {html.escape(label)} ({len(group)} component"
            f"{'s' if len(group) != 1 else ''})</td></tr>"
        )
        for r in group:
            cells = [
                f"<td>{_container_link(base_url, r['key'])}</td>",
                f"<td>{_source_badge(r['sources'])}</td>",
                f"<td>{_esc(r['article'])}</td>",
                f"<td>{_order_type_badge(r['order_type'])}</td>",
                f"<td>{_esc(r['component'])}</td>",
                _plc_cell(r["plc"]),
                f"<td>{_esc(r['description'])}</td>",
            ]
            body_parts.append("<tr>" + "".join(cells) + "</tr>")

    return "<table><tbody>" + "".join(body_parts) + "</tbody></table>"


def _render_clean_section(base_url: str, clean: list[dict[str, Any]]) -> str:
    if not clean:
        return "<p><em>None.</em></p>"
    header = (
        "<tr><th>Container</th><th>Source</th>"
        "<th>Articles</th></tr>"
    )
    body_rows: list[str] = []
    for c in clean:
        articles_html = ", ".join(
            f"{html.escape(a['article_number'])} "
            f"{_order_type_badge(a.get('order_type'))}"
            for a in c["articles"]
        ) or "-"
        body_rows.append(
            "<tr>"
            f"<td>{_container_link(base_url, c['key'])}</td>"
            f"<td>{_source_badge(c['sources'])}</td>"
            f"<td>{articles_html}</td>"
            "</tr>"
        )
    return "<table><tbody>" + header + "".join(body_rows) + "</tbody></table>"


def _render_skipped_section(base_url: str, skipped: list[dict[str, Any]]) -> str:
    if not skipped:
        return "<p><em>None.</em></p>"
    header = (
        "<tr><th>Container</th><th>Source</th>"
        "<th>Reason</th></tr>"
    )
    body_rows: list[str] = []
    for s in skipped:
        reasons = "; ".join(html.escape(r) for r in s["reasons"]) or "-"
        body_rows.append(
            "<tr>"
            f"<td>{_container_link(base_url, s['key'])}</td>"
            f"<td>{_source_badge(s['sources'])}</td>"
            f"<td>{reasons}</td>"
            "</tr>"
        )
    return "<table><tbody>" + header + "".join(body_rows) + "</tbody></table>"


def _render_summary(
    results: list[dict[str, Any]],
    buckets: dict[str, list],
    target_status: str,
) -> str:
    total = len(results)
    flagged_containers = sum(
        1 for r in results
        if any(a.get("flagged") for a in (r.get("articles") or []))
    )
    jira_only = sum(1 for r in results if set(r.get("sources") or []) == {"jira"})
    conf_only = sum(1 for r in results if set(r.get("sources") or []) == {"confluence"})
    both = sum(
        1 for r in results
        if {"jira", "confluence"}.issubset(set(r.get("sources") or []))
    )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    spi_components = len(buckets["spi_rows"])
    sno_components = len(buckets["sno_rows"])

    header = (
        '<table><tbody>'
        '<tr><th>Scan time</th><th>Target PLC</th>'
        '<th>Containers</th><th>Flagged</th>'
        '<th>SPI components</th><th>SNO components</th>'
        '<th>JIRA only</th><th>Confluence only</th><th>Both</th></tr>'
        "<tr>"
        f"<td>{html.escape(generated)}</td>"
        f"<td><strong>{html.escape(target_status)}</strong></td>"
        f"<td>{total}</td>"
        f"<td>{flagged_containers}</td>"
        f"<td>{spi_components}</td>"
        f"<td>{sno_components}</td>"
        f"<td>{jira_only}</td>"
        f"<td>{conf_only}</td>"
        f"<td>{both}</td>"
        "</tr>"
        "</tbody></table>"
    )
    return "<h2>Summary</h2>" + header


def _run_instructions_html(target_status: str) -> str:
    scan_cmd = RUN_PS_CMD.replace(
        "--target-status 310", f"--target-status {target_status}",
    )
    comment_cmd = COMMENT_PS_CMD.replace(
        "--target-status 310", f"--target-status {target_status}",
    )
    return (
        "<h2>Commands</h2>"
        "<p><strong>Re-run the scan</strong> (refreshes this page, no JIRA writes):</p>"
        + _code_macro("text", RUN_BAT_PATH)
        + _code_macro("powershell", scan_cmd)
        + "<p><strong>Post a BOM comment on chosen containers</strong> "
        "(replace KEY1/KEY2 with the keys from the tables above):</p>"
        + _code_macro("powershell", comment_cmd)
    )


# ── Public renderer ──────────────────────────────────────────────────

def results_to_html(
    results: list[dict[str, Any]],
    target_status: str,
    jira_base_url: str,
) -> str:
    """Render scanner results as Confluence storage-format HTML."""
    buckets = _classify_results(results)

    summary_block = _render_summary(results, buckets, target_status)

    spi_block = (
        "<h2>Primary Articles (SPI)</h2>"
        + _render_flag_table(jira_base_url, buckets["spi_rows"])
    )
    sno_block = (
        "<h2>Reference Articles (SNO)</h2>"
        + _render_flag_table(jira_base_url, buckets["sno_rows"])
    )

    clean_count = len(buckets["clean"])
    clean_block = (
        "<h2>Clean Containers</h2>"
        + _expand_macro(
            f"Show {clean_count} clean container{'s' if clean_count != 1 else ''}",
            _render_clean_section(jira_base_url, buckets["clean"]),
        )
    )

    skipped_count = len(buckets["skipped"])
    skipped_block = (
        "<h2>Skipped Containers</h2>"
        + _expand_macro(
            f"Show {skipped_count} skipped container{'s' if skipped_count != 1 else ''}",
            _render_skipped_section(jira_base_url, buckets["skipped"]),
        )
    )

    footer_block = _run_instructions_html(target_status)

    return (
        summary_block
        + spi_block
        + sno_block
        + clean_block
        + skipped_block
        + "<hr/>"
        + footer_block
    )


def publish_results(
    config: Config,
    results: list[dict[str, Any]],
    target_status: str,
) -> str:
    """Push the results table to Confluence. Returns the page URL."""
    page_id = config.pages.get(PAGE_KEY)
    if not page_id:
        raise FriendlyError(
            f"No Confluence page ID configured for '{PAGE_KEY}'",
            f"Add `pages.{PAGE_KEY}: <id>` to config/config.yaml",
        )

    if config.is_mock:
        logger.warning(
            "Publish: skipping in mock mode (would push %d result(s) to page %s)",
            len(results),
            page_id,
        )
        return ""

    client = ConfluenceClient(config)
    current = client.get_page(page_id)
    title = current.get("title") or "BOM Scanner"

    html_body = results_to_html(
        results,
        target_status=target_status,
        jira_base_url=config.jira_base_url,
    )

    logger.info("Publishing %d result(s) to Confluence page %s", len(results), page_id)
    result = client.update_page(page_id, title=title, html_body=html_body)

    version = (result.get("version") or {}).get("number", "?")
    page_url = (
        f"{config.confluence_base_url.rstrip('/')}"
        f"/pages/viewpage.action?pageId={page_id}"
    )
    logger.info("Published v%s -> %s", version, page_url)
    return page_url
