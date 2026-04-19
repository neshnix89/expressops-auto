"""
Publish to_status_check results to Confluence.

Uses core.confluence.ConfluenceClient (Bearer PAT to
pfteamspace.pepperl-fuchs.com) — same auth pattern as the existing MR
Status Report and ExpressOPS KPI pipeline.

Target page ID comes from config.pages['to_status_check'].
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from core.config_loader import Config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError
from core.logger import get_logger

logger = get_logger("to_status_check.publish")

PAGE_KEY = "to_status_check"


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def rows_to_html(rows: list[dict[str, Any]], include_m3: bool) -> str:
    """Render container rows as a Confluence storage-format HTML table."""
    if not rows:
        return "<p><em>No active Work Containers.</em></p>"

    headers = ["Container", "Status", "TO Number"]
    if include_m3:
        headers += [
            "TO Status",
            "Sending Site",
            "Receiving Site",
            "Ready to Close",
        ]
    headers += ["Summary"]

    header_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows: list[str] = []
    for r in rows:
        cells = [
            _cell(r.get("key")),
            _cell(r.get("status")),
            _cell(r.get("to_number")),
        ]
        if include_m3:
            cells += [
                _cell(r.get("to_status")),
                _cell(r.get("to_sending_site")),
                _cell(r.get("to_receiving_site")),
                "Yes" if r.get("ready_to_close") else "No",
            ]
        cells += [_cell((r.get("summary") or "")[:80])]
        body_rows.append(
            "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    with_to = sum(1 for r in rows if r.get("has_to"))
    ready = sum(1 for r in rows if r.get("ready_to_close"))

    return (
        f"<p><strong>TO Status Check</strong> — generated {generated}</p>"
        f"<p>Total: {total} &nbsp; With TO: {with_to} &nbsp; "
        f"Ready to close: {ready}</p>"
        "<table><tbody>"
        f"<tr>{header_html}</tr>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def publish_results(
    config: Config,
    rows: list[dict[str, Any]],
    include_m3: bool,
) -> str:
    """
    Publish the result table to Confluence. Returns the page URL.

    Raises FriendlyError if no target page is configured.
    """
    page_id = config.pages.get(PAGE_KEY)
    if not page_id:
        raise FriendlyError(
            f"No Confluence page ID configured for '{PAGE_KEY}'",
            f"Add `pages.{PAGE_KEY}: <id>` to config/config.yaml",
        )

    if config.is_mock:
        logger.warning(
            "Publish: skipping in mock mode (would push %d row(s) to page %s)",
            len(rows),
            page_id,
        )
        return ""

    client = ConfluenceClient(config)
    current = client.get_page(page_id)
    title = current.get("title") or "TO Status Check"

    html_body = rows_to_html(rows, include_m3=include_m3)

    logger.info("Publishing %d row(s) to Confluence page %s", len(rows), page_id)
    result = client.update_page(page_id, title=title, html_body=html_body)

    version = (result.get("version") or {}).get("number", "?")
    page_url = (
        f"{config.confluence_base_url.rstrip('/')}"
        f"/pages/viewpage.action?pageId={page_id}"
    )
    logger.info("Published v%s -> %s", version, page_url)
    return page_url
