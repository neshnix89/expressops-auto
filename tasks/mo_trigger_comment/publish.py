"""
Publish mo_trigger_comment results to Confluence.

The staging page gives the planner a single place to review every
container that's ready for an MO trigger:

    1. Summary banner          — run time, totals (ready / skipped)
    2. Ready for MO trigger    — per-container rows with key, summary,
                                 order type, MO window, badges, and the
                                 assembled comment body inside an expand
                                 macro so the page stays readable.
    3. Skipped containers      — collapsed, with the skip reason so an
                                 operator can tell at a glance why a
                                 container didn't make the cut.
    4. Footer                  — code macros showing the re-run and
                                 JIRA-post commands for the company laptop.

The target page ID lives in ``config.pages['mo_trigger_comment']`` so a
config change is the only thing needed to re-point the publisher.
"""

from __future__ import annotations

import html
import hashlib
import re
from datetime import datetime
from typing import Any

from core.config_loader import Config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError
from core.logger import get_logger

logger = get_logger("mo_trigger_comment.publish")

PAGE_KEY = "mo_trigger_comment"

RUN_BAT_PATH = r"C:\Users\tmoghanan\Documents\AI\expressops-auto\scripts\mo_trigger_comment_run.bat"
RUN_PS_CMD = (
    r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe "
    r"-m tasks.mo_trigger_comment.main run --live --publish"
)
COMMENT_PS_CMD = (
    r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe "
    r"-m tasks.mo_trigger_comment.main comment --live --keys KEY1 KEY2"
)


# ── Macro + escape helpers ───────────────────────────────────────────


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


def _expand_macro(title: str, body_html: str, extra_params: str = "") -> str:
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
        f'{extra_params}'
        f'<ac:rich-text-body>{body_html}</ac:rich-text-body>'
        '</ac:structured-macro>'
    )


def _norm_body(s: str) -> str:
    """Whitespace-normalise a comment body so trivial reflow in the Confluence
    editor doesn't read as a human edit."""
    lines = [ln.rstrip() for ln in (s or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _body_hash(s: str) -> str:
    return hashlib.sha1(_norm_body(s).encode("utf-8")).hexdigest()[:12]


def _status_macro(colour: str, title: str) -> str:
    return (
        '<ac:structured-macro ac:name="status">'
        f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
        '</ac:structured-macro>'
    )


def _container_link(base_url: str, key: str) -> str:
    href = f"{base_url.rstrip('/')}/browse/{key}"
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(key)}</a>'


def _pilot_badge(is_pilot: bool) -> str:
    return _status_macro("Yellow", "PILOT") if is_pilot else ""


def _programme_ic_badge(is_ic: bool) -> str:
    return _status_macro("Purple", "PROGRAMME IC") if is_ic else ""


# ── Section renderers ────────────────────────────────────────────────


_READY_HEADERS = (
    "Container", "Summary", "Order Type",
    "MO Start", "MO End", "Flags", "Articles", "Comment",
)


def _render_ready_section(
    base_url: str,
    ready: list[dict[str, Any]],
) -> str:
    if not ready:
        return "<p><em>No containers are currently ready for MO trigger.</em></p>"

    header_html = "".join(f"<th>{html.escape(h)}</th>" for h in _READY_HEADERS)
    body_rows: list[str] = []

    for r in ready:
        badges = " ".join(
            b for b in (_pilot_badge(r.get("is_pilot", False)),
                        _programme_ic_badge(r.get("is_programme_ic", False)))
            if b
        ) or "-"
        articles = ", ".join(r.get("articles") or []) or "-"

        body = r.get("body") or ""
        # Stamp the hash of the body the machine wrote (or the preserved
        # original hash, threaded in via _mohash) so the next run can tell a
        # human edit from its own previous output.
        bhash = r.get("_mohash") or _body_hash(body)
        comment_cell = _expand_macro(
            f"Show comment for {r.get('key', '?')}",
            _code_macro("text", body),
            extra_params=f'<ac:parameter ac:name="mohash">{bhash}</ac:parameter>',
        )

        cells = [
            f"<td>{_container_link(base_url, r.get('key', '?'))}</td>",
            f"<td>{_esc(r.get('summary'))}</td>",
            f"<td>{_esc(r.get('order_type'))}</td>",
            f"<td>{_esc(r.get('mo_start'))}</td>",
            f"<td>{_esc(r.get('mo_end'))}</td>",
            f"<td>{badges}</td>",
            f"<td>{html.escape(articles)}</td>",
            f"<td>{comment_cell}</td>",
        ]
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        "<table><tbody>"
        + f"<tr>{header_html}</tr>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


_SKIPPED_HEADERS = ("Container", "Summary", "Order Type", "Ready?", "Reason")


def _render_skipped_section(
    base_url: str,
    skipped: list[dict[str, Any]],
) -> str:
    if not skipped:
        return "<p><em>None.</em></p>"
    header_html = "".join(f"<th>{html.escape(h)}</th>" for h in _SKIPPED_HEADERS)
    body_rows: list[str] = []
    for r in skipped:
        ready_badge = (
            _status_macro("Green", "READY")
            if r.get("ready") else _status_macro("Grey", "NOT READY")
        )
        cells = [
            f"<td>{_container_link(base_url, r.get('key', '?'))}</td>",
            f"<td>{_esc(r.get('summary'))}</td>",
            f"<td>{_esc(r.get('order_type'))}</td>",
            f"<td>{ready_badge}</td>",
            f"<td>{_esc(r.get('skip_reason'))}</td>",
        ]
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<table><tbody>"
        + f"<tr>{header_html}</tr>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def _render_summary(
    results: list[dict[str, Any]],
    ready: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    pilot_count = sum(1 for r in ready if r.get("is_pilot"))
    ic_count = sum(1 for r in ready if r.get("is_programme_ic"))
    header = (
        '<table><tbody>'
        '<tr><th>Run time</th><th>Scanned</th><th>Ready</th>'
        '<th>Pilot</th><th>Programme IC</th><th>Skipped</th></tr>'
        "<tr>"
        f"<td>{html.escape(generated)}</td>"
        f"<td>{len(results)}</td>"
        f"<td><strong>{len(ready)}</strong></td>"
        f"<td>{pilot_count}</td>"
        f"<td>{ic_count}</td>"
        f"<td>{len(skipped)}</td>"
        "</tr></tbody></table>"
    )
    return "<h2>Summary</h2>" + header


def _run_instructions_html() -> str:
    return (
        "<h2>Commands</h2>"
        "<p><strong>Re-run and refresh this page</strong> "
        "(read-only against JIRA + M3):</p>"
        + _code_macro("text", RUN_BAT_PATH)
        + _code_macro("powershell", RUN_PS_CMD)
        + "<p><strong>Post an MO-trigger comment on chosen containers</strong> "
        "(replace KEY1/KEY2 with the container keys from the table above):</p>"
        + _code_macro("powershell", COMMENT_PS_CMD)
    )


# ── Preserving manual edits on re-publish ────────────────────────────

# Matches the per-container expand/code macro that _render_ready_section
# emits for each ready row. Whitespace-tolerant because Confluence may reflow
# the stored form after a human edit:
#   <ac:parameter ac:name="title">Show comment for {KEY}</ac:parameter>
#   <ac:parameter ac:name="mohash">{HASH}</ac:parameter>   (optional; legacy
#       rows published before this change have no mohash)
#   ... <ac:plain-text-body><![CDATA[{BODY}]]></ac:plain-text-body>
_STAGED_COMMENT_RE = re.compile(
    r'<ac:parameter\s+ac:name="title">\s*'
    r'Show comment for ([\w-]+)\s*'
    r'</ac:parameter>'
    r'(?:\s*<ac:parameter\s+ac:name="mohash">\s*([0-9a-f]+)\s*</ac:parameter>)?'
    r'.*?<!\[CDATA\[(.*?)\]\]>',
    re.DOTALL,
)


def _parse_staged_bodies(html_body: str) -> dict[str, dict[str, Any]]:
    """
    Pull ``{key: {"body": <stored body>, "hash": <machine hash or None>}}``
    from the staging page's storage-format HTML. ``hash`` is the mohash the
    machine stamped when it last wrote that body (None for legacy rows).
    """
    if not html_body:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for m in _STAGED_COMMENT_RE.finditer(html_body):
        out[m.group(1)] = {"body": m.group(3).rstrip(), "hash": m.group(2)}
    return out


def _merge_preserved_bodies(
    results: list[dict[str, Any]],
    staged: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Refresh every body from the current run EXCEPT where a human actually
    edited the body on the page.

    Each published body carries a ``mohash`` (hash of the body the machine
    last wrote). On re-publish we hash the body currently on the page:
      * matches mohash            -> untouched -> use the fresh body
      * differs                   -> human-edited -> keep their text, and carry
                                     the ORIGINAL mohash so it stays preserved
      * no mohash (legacy row)    -> treat as machine-written -> refresh

    The body the renderer should stamp travels back in ``_mohash``. Returns
    (merged, preserved_count) where preserved_count counts human-edited rows.
    """
    if not staged:
        return list(results), 0
    merged: list[dict[str, Any]] = []
    preserved_count = 0
    for r in results:
        key = r.get("key")
        if r.get("ready") and r.get("body") and key in staged:
            stored_body = staged[key]["body"]
            stored_hash = staged[key]["hash"]
            edited = stored_hash is not None and _body_hash(stored_body) != stored_hash
            if edited:
                merged.append({**r, "body": stored_body, "_mohash": stored_hash})
                preserved_count += 1
            else:
                merged.append({**r, "_mohash": _body_hash(r["body"])})
        else:
            merged.append(r)
    return merged, preserved_count


# ── Public renderer ──────────────────────────────────────────────────


def _split(results: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Split into (ready_with_comment, skipped)."""
    ready = [r for r in results if r.get("ready") and r.get("body")]
    skipped = [r for r in results if not (r.get("ready") and r.get("body"))]
    return ready, skipped


def results_to_html(
    results: list[dict[str, Any]],
    jira_base_url: str,
) -> str:
    """Render Phase-1 results as Confluence storage-format HTML."""
    ready, skipped = _split(results)

    summary_block = _render_summary(results, ready, skipped)
    ready_block = (
        "<h2>Ready for MO trigger</h2>"
        + _render_ready_section(jira_base_url, ready)
    )
    skipped_block = (
        "<h2>Skipped Containers</h2>"
        + _expand_macro(
            f"Show {len(skipped)} skipped container{'s' if len(skipped) != 1 else ''}",
            _render_skipped_section(jira_base_url, skipped),
        )
    )
    footer_block = _run_instructions_html()

    return (
        summary_block
        + ready_block
        + skipped_block
        + "<hr/>"
        + footer_block
    )


def publish_results(
    config: Config,
    results: list[dict[str, Any]],
) -> str:
    """Push the staging table to Confluence. Returns the page URL."""
    page_id = config.pages.get(PAGE_KEY)
    if not page_id:
        raise FriendlyError(
            f"No Confluence page ID configured for '{PAGE_KEY}'",
            f"Add `pages.{PAGE_KEY}: <id>` to config/config.yaml",
        )

    if config.is_mock:
        logger.warning(
            "Publish: skipping in mock mode (would push %d result(s) to page %s)",
            len(results), page_id,
        )
        return ""

    client = ConfluenceClient(config)
    current = client.get_page(page_id)
    title = current.get("title") or "MO Trigger Comments"

    # Preserve any manually edited comment bodies already on the page.
    # The summary and row metadata are always regenerated from the
    # current run; only the body text a planner may have edited is kept.
    existing_html = (current.get("body") or {}).get("storage", {}).get("value", "") or ""
    preserved = _parse_staged_bodies(existing_html)
    results_for_render, preserved_count = _merge_preserved_bodies(results, preserved)
    if preserved_count:
        logger.info(
            "Preserving %d existing comment body/ies from Confluence page %s",
            preserved_count, page_id,
        )

    html_body = results_to_html(results_for_render, jira_base_url=config.jira_base_url)

    logger.info("Publishing %d result(s) to Confluence page %s", len(results), page_id)
    result = client.update_page(page_id, title=title, html_body=html_body)

    version = (result.get("version") or {}).get("number", "?")
    page_url = (
        f"{config.confluence_base_url.rstrip('/')}"
        f"/pages/viewpage.action?pageId={page_id}"
    )
    logger.info("Published v%s -> %s", version, page_url)
    return page_url
