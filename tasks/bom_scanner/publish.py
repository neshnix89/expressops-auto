"""
Publish bom_scanner results to Confluence.

Same auth / page-update pattern as tasks/to_status_check/publish.py:
Bearer PAT against pfteamspace.pepperl-fuchs.com, target page pulled
from config.pages['bom_scanner'].
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from core.config_loader import Config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError
from core.logger import get_logger

from tasks.bom_scanner.logic import build_confluence_rows, summarize

logger = get_logger("bom_scanner.publish")

PAGE_KEY = "bom_scanner"

RUN_BAT_PATH = r"C:\Users\tmoghanan\Documents\AI\expressops-auto\scripts\bom_scanner_run.bat"
RUN_PS_CMD = (
    r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe "
    r"-m tasks.bom_scanner.main --live --target-status 310 --publish"
)


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def _code_macro(language: str, body: str) -> str:
    """Confluence storage-format code macro with CDATA body."""
    return (
        '<ac:structured-macro ac:name="code">'
        f'<ac:parameter ac:name="language">{language}</ac:parameter>'
        f'<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>'
        '</ac:structured-macro>'
    )


def _run_instructions_html(target_status: str) -> str:
    """Footer block explaining how to re-trigger the scanner."""
    ps_cmd = RUN_PS_CMD.replace("--target-status 310", f"--target-status {target_status}")
    return (
        "<hr/>"
        "<p>To re-run the BOM Scanner (posts JIRA comments on flagged containers):</p>"
        "<p><strong>Batch script:</strong></p>"
        + _code_macro("text", RUN_BAT_PATH)
        + "<p><strong>Manual command:</strong></p>"
        + _code_macro("powershell", ps_cmd)
    )


def results_to_html(
    results: list[dict[str, Any]],
    target_status: str,
) -> str:
    """Render scanner results as Confluence storage-format HTML."""
    rows = build_confluence_rows(results)
    summary = summarize(results)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    header_line = (
        f"<p><strong>BOM Scanner</strong> \u2014 generated {generated} "
        f"(target PLC = {html.escape(target_status)})</p>"
    )
    stats_line = (
        f"<p>Total: {summary['total']} &nbsp; "
        f"With article #: {summary['with_articles']} &nbsp; "
        f"Flagged: {summary['flagged_containers']} &nbsp; "
        f"Commented: {summary['commented']}</p>"
    )

    if not rows:
        return (
            header_line
            + stats_line
            + "<p><em>No containers scanned.</em></p>"
            + _run_instructions_html(target_status)
        )

    headers = [
        "Container",
        "Source",
        "Article #",
        "Flagged",
        "Component Details",
        "Reporter",
        "Action Taken",
    ]
    header_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)

    body_rows: list[str] = []
    for r in rows:
        cells = [
            _cell(r["key"]),
            _cell(r["sources"]),
            _cell(r["article"]),
            _cell(r["flagged_count"]),
            _cell(r["component_details"]),
            _cell(r["reporter"]),
            _cell(r["action_taken"]),
        ]
        body_rows.append(
            "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        )

    return (
        header_line
        + stats_line
        + "<table><tbody>"
        + f"<tr>{header_html}</tr>"
        + "".join(body_rows)
        + "</tbody></table>"
        + _run_instructions_html(target_status)
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

    html_body = results_to_html(results, target_status=target_status)

    logger.info("Publishing %d result(s) to Confluence page %s", len(results), page_id)
    result = client.update_page(page_id, title=title, html_body=html_body)

    version = (result.get("version") or {}).get("number", "?")
    page_url = (
        f"{config.confluence_base_url.rstrip('/')}"
        f"/pages/viewpage.action?pageId={page_id}"
    )
    logger.info("Published v%s -> %s", version, page_url)
    return page_url
