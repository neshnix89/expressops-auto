#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gdc_transfer_check.py
---------------------
Daily check for NPI SMT PCBA Singapore containers.
Identifies BOM components at GDC needing transfer to MOPS1.

Commands:
    scan              Read-only. Publishes status to Confluence only.
    send              Full run: email warehouse + post JIRA cooldown markers.
    send --dry-run    Full run without email or JIRA writes.

Scheduled: daily 8:00 AM
"""

import sys
import re
import html as html_lib
import pyodbc
import requests
import urllib3
import yaml
from datetime import date, datetime, timedelta
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Path setup ────────────────────────────────────────────────────────────────
TASK_DIR    = Path(__file__).parent
REPO_ROOT   = TASK_DIR.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"

# ── Constants ─────────────────────────────────────────────────────────────────
MO_PATTERN      = re.compile(r'\b700\d{7}\b')
ACCEPTABLE_LOCS = {'MOPS1', 'D-MOPS1'}   # stock here = no transfer needed
PRIMARY_GDC     = 'GDC'                   # request transfer from this loc
OTHER_GDC_LOCS  = {'GDC-IQN', 'GDC-LOCKED', 'GDC MANUAL'}  # flag only

COOLDOWN_RE = re.compile(
    r'#GDC-TRANSFER: PN=([^,]+), Triggered=(\d{4}-\d{2}-\d{2})#'
)
TEMPLATE_RE = re.compile(
    r'#GDC-TEMPLATE-MISSING: Notified=(\d{4}-\d{2}-\d{2})#'
)

JQL = (
    'issuetype = "Work Container" '
    'AND "Product Type" = "SMT PCBA" '
    'AND "NPI Location" = "Singapore" '
    'AND resolution is EMPTY '
    'ORDER BY created ASC'
)

# ── Config ────────────────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return yaml.safe_load(f)

# ── JIRA helpers ──────────────────────────────────────────────────────────────
def make_jira_session(cfg):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {cfg['jira']['pat']}",
        "Content-Type": "application/json"
    })
    s.verify = False
    return s

def search_all(session, base, jql, fields, expand=None):
    results, start = [], 0
    while True:
        params = {"jql": jql, "fields": ",".join(fields),
                  "startAt": start, "maxResults": 50}
        if expand:
            params["expand"] = expand
        r = session.get(f"{base}/rest/api/2/search", params=params)
        r.raise_for_status()
        data   = r.json()
        issues = data.get("issues", [])
        results.extend(issues)
        if start + len(issues) >= data.get("total", 0):
            break
        start += 50
    return results

def get_issue(session, base, key, fields):
    r = session.get(
        f"{base}/rest/api/2/issue/{key}",
        params={"fields": ",".join(fields)}
    )
    r.raise_for_status()
    return r.json()

def post_comment(session, base, key, body, dry_run=False):
    if dry_run:
        print(f"    [DRY-RUN] Comment to {key}: {body[:120].strip()}...")
        return True
    r = session.post(
        f"{base}/rest/api/2/issue/{key}/comment",
        json={"body": body}
    )
    if r.status_code == 201:
        return True
    print(f"    [WARN] Comment post failed on {key}: {r.status_code}")
    return False

# ── Stop condition ────────────────────────────────────────────────────────────
def has_mo_number(comments):
    for c in comments:
        if MO_PATTERN.search(c.get("body", "") or ""):
            return True
    return False

# ── Description parser: NPI Built Type & Quantities table ────────────────────
def parse_npi_table(description_html):
    """
    Parse NPI Built Type & Quantities table from rendered HTML description.
    Fetch description with expand=renderedFields for this to work.
    Returns list of {"pn": str, "request_qty": int} or None if not found.
    """
    if not description_html:
        return None

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(description_html, "html.parser")

    # Find panel header containing "NPI Built Type"
    panel_header = None
    for div in soup.find_all("div", class_="panelHeader"):
        if "npi built type" in div.get_text().lower():
            panel_header = div
            break
    # Fallback: plain bold text (some containers use <b> without panel)
    if panel_header is None:
        for tag in soup.find_all(["b", "strong", "h2", "h3"]):
            if "npi built type" in tag.get_text().lower():
                panel_header = tag
                break

    if panel_header is None:
        return None

    # Find the next confluenceTable after the header
    table = panel_header.find_next("table", class_="confluenceTable")
    if table is None:
        # Try any table after the header
        table = panel_header.find_next("table")
    if table is None:
        return None

    trs = table.find_all("tr")
    pn_idx  = None
    qty_idx = None
    rows    = []

    for tr in trs:
        # Header row — find column indices
        headers = tr.find_all(["th"])
        if not headers:
            # Some tables use <td><b> for headers
            cells = tr.find_all("td")
            if cells and all(c.find("b") for c in cells if c.get_text(strip=True)):
                headers = cells

        if headers and pn_idx is None:
            for i, h in enumerate(headers):
                text = h.get_text(strip=True).lower()
                if "pn" in text or "part" in text:
                    pn_idx = i
                if "request" in text and "qty" in text:
                    qty_idx = i
            continue

        if pn_idx is None or qty_idx is None:
            continue

        # Data row
        tds = tr.find_all("td")
        if len(tds) <= max(pn_idx, qty_idx):
            continue

        # PN: may be inside <a> tag, may have # prefix
        pn_cell = tds[pn_idx]
        a_tag   = pn_cell.find("a")
        if a_tag:
            pn_val = a_tag.get_text(strip=True).lstrip("#")
        else:
            pn_val = pn_cell.get_text(strip=True).lstrip("#")

        if not pn_val:
            continue

        # Qty: strip any strikethrough <del> tags (take <b> value if present)
        qty_cell = tds[qty_idx]
        bold     = qty_cell.find("b")
        qty_text = bold.get_text(strip=True) if bold else qty_cell.get_text(strip=True)
        qty_text = qty_text.replace(",", "").strip()

        if not qty_text or qty_text == "0":
            continue

        try:
            qty_int = int(float(qty_text))
            if qty_int > 0:
                rows.append({"pn": pn_val, "request_qty": qty_int})
        except (ValueError, TypeError):
            continue

    return rows if rows else None

# ── Working day calculator ────────────────────────────────────────────────────
def working_days_since(date_str):
    """Mon–Fri working days elapsed since date_str (YYYY-MM-DD) up to today."""
    try:
        past = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return 999  # unparseable = treat as expired
    today   = date.today()
    if past >= today:
        return 0
    count   = 0
    current = past + timedelta(days=1)
    while current <= today:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count

# ── Cooldown state ────────────────────────────────────────────────────────────
def get_cooldown_state(comments, cooldown_days):
    """
    Returns {pn: triggered_date_str} for PNs whose cooldown is still active.
    If same PN appears multiple times, the most recent trigger date wins.
    """
    latest = {}
    for c in comments:
        body = c.get("body", "") or ""
        for m in COOLDOWN_RE.finditer(body):
            pn, triggered = m.group(1), m.group(2)
            if pn not in latest or triggered > latest[pn]:
                latest[pn] = triggered
    return {
        pn: dt for pn, dt in latest.items()
        if working_days_since(dt) < cooldown_days
    }

def get_template_notice_date(comments):
    """Returns the most recent #GDC-TEMPLATE-MISSING Notified date, or None."""
    latest = None
    for c in comments:
        body = c.get("body", "") or ""
        m = TEMPLATE_RE.search(body)
        if m:
            d = m.group(1)
            if latest is None or d > latest:
                latest = d
    return latest

def get_all_trigger_dates(comments):
    """
    Returns {pn: latest_triggered_date_str} regardless of cooldown status.
    Used to detect repeat requests.
    """
    latest = {}
    for c in comments:
        body = c.get("body", "") or ""
        for m in COOLDOWN_RE.finditer(body):
            pn, triggered = m.group(1), m.group(2)
            if pn not in latest or triggered > latest[pn]:
                latest[pn] = triggered
    return latest

# ── M3 ODBC ───────────────────────────────────────────────────────────────────
def fetch_bom(conn, article, warehouse="MF1"):
    """
    Returns {component_pn: total_qty} for a finished good article.
    Excludes packaging materials (description starts with 'PM').
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.PMMTNO, SUM(m.PMCNQT) AS TOTAL_QTY
        FROM PFODS.MPDMAT m
        JOIN PFODS.MITMAS_AP i ON i.MMITNO = m.PMMTNO
        WHERE TRIM(m.PMPRNO) = ?
          AND TRIM(m.PMSTRT) = 'STD'
          AND TRIM(m.PMFACI) = ?
          AND TRIM(i.MMITDS) NOT LIKE 'PM %'
        GROUP BY m.PMMTNO
        """,
        (article.strip(), warehouse)
    )
    return {row[0].strip(): float(row[1] or 0) for row in cur.fetchall()}

def fetch_stock(conn, pn, warehouse="MF1"):
    """Returns {sub_location: available_qty} where available = on_hand - allocated."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MBWHSL, MBSTQT, MBALQT
        FROM PFODS.MITBAL
        WHERE TRIM(MBITNO) = ?
          AND MBWHLO = ?
        """,
        (pn.strip(), warehouse)
    )
    result = {}
    for row in cur.fetchall():
        loc       = (row[0] or "").strip()
        available = float(row[1] or 0) - float(row[2] or 0)
        result[loc] = result.get(loc, 0) + available
    return result

def fetch_item_desc(conn, pn):
    """Returns item description from MITMAS_AP, or empty string."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT MMITDS FROM PFODS.MITMAS_AP WHERE TRIM(MMITNO) = ?",
            (pn.strip(),)
        )
        row = cur.fetchone()
        return row[0].strip() if row else ""
    except Exception:
        return ""

# ── Email ─────────────────────────────────────────────────────────────────────
def send_transfer_email(to_addr, transfer_list, dry_run=False):
    today_str = date.today().strftime("%d-%b-%Y")
    subject   = f"[EXPRESS OPS] GDC\u2192MOPS1 Transfer Request \u2014 {today_str}"

    # Group by container
    by_container = {}
    for item in transfer_list:
        by_container.setdefault(item["container_key"], []).append(item)

    # ── Build HTML body ───────────────────────────────────────────────────
    style = """
        body { font-family: Calibri, Arial, sans-serif; font-size: 14px; color: #1a1a1a; }
        h2   { font-size: 15px; color: #1a3c6e; margin: 20px 0 4px 0; }
        p    { margin: 4px 0; }
        .priority { color: #c00000; font-weight: bold; }
        .followup { color: #c00000; font-style: italic; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
        th { background-color: #1a3c6e; color: white; padding: 6px 10px;
             text-align: left; font-size: 13px; }
        td { padding: 5px 10px; font-size: 13px; border-bottom: 1px solid #e0e0e0; }
        tr:nth-child(even) { background-color: #f5f7fa; }
        .container-header { background-color: #e8edf5; padding: 8px 10px;
                            margin-top: 20px; border-left: 4px solid #1a3c6e; }
        .footer { margin-top: 24px; color: #666; font-size: 12px; }
        .total  { font-weight: bold; margin-top: 8px; }
    """

    html_parts = [
        f"<html><head><style>{style}</style></head><body>",
        f"<p class='priority'>Priority: High &mdash; Express OPS NPI Build Material Readiness</p>",
        f"<p>Please arrange transfer of the following items to <strong>MOPS1</strong> "
        f"(SG1 warehouse, MF1) for upcoming NPI builds.</p>"
        f"<p>Please create <strong>/80 relocation orders</strong> in M3 for each line.</p>",
    ]

    total_lines = 0
    for ckey, items in by_container.items():
        # Deduplicate by PN — sum shortfall, skip qty 0
        deduped = {}
        for item in items:
            if item["shortfall"] <= 0:
                continue
            pn = item["pn"]
            if pn not in deduped:
                deduped[pn] = {
                    "pn":              pn,
                    "pn_desc":         item.get("pn_desc", ""),
                    "shortfall":       item["shortfall"],
                    "source_location": item["source_location"],
                    "flagged_locs":    item.get("flagged_locs", []),
                    "prev_trigger":    item.get("prev_trigger"),
                }
            else:
                deduped[pn]["shortfall"] += item["shortfall"]

        if not deduped:
            continue

        all_articles = ", ".join(dict.fromkeys(i["article"] for i in items))

        # Follow-up detection
        prev_dates = [i["prev_trigger"] for i in items if i.get("prev_trigger")]
        if prev_dates:
            earliest_prev = min(prev_dates)
            wd_ago        = working_days_since(earliest_prev)
            followup_html = (
                f"<br><span class='followup'>&#9888; FOLLOW-UP &mdash; "
                f"previously requested {earliest_prev} "
                f"({wd_ago} working day(s) ago, stock still not at MOPS1)</span>"
            )
        else:
            followup_html = ""

        html_parts.append(
            f"<div class='container-header'>"
            f"<strong>{ckey}</strong> &nbsp;|&nbsp; Article(s): {all_articles}"
            f"{followup_html}</div>"
        )
        html_parts.append(
            "<table>"
            "<tr>"
            "<th>Item No.</th>"
            "<th>Description</th>"
            "<th>From</th>"
            "<th style='text-align:right;'>Qty to Transfer</th>"
            "</tr>"
        )

        for item in deduped.values():
            desc     = item.get("pn_desc", "")
            src      = item["source_location"]
            qty      = int(item["shortfall"])
            flag_note = ""
            if item.get("flagged_locs"):
                flag_note = (
                    f"<br><span style='color:#888;font-size:11px;'>"
                    f"Also at: {', '.join(item['flagged_locs'])} (not requestable)</span>"
                )
            html_parts.append(
                f"<tr>"
                f"<td>{item['pn']}</td>"
                f"<td>{desc}{flag_note}</td>"
                f"<td>{src}</td>"
                f"<td style='text-align:right;'><strong>{qty}</strong></td>"
                f"</tr>"
            )
            total_lines += 1

        html_parts.append("</table>")

    html_parts += [
        f"<p class='total'>Total: {total_lines} line(s) across "
        f"{len(by_container)} container(s).</p>",
        "<div class='footer'>Regards,<br>"
        "Express OPS Automation (gdc_transfer_check)</div>",
        "</body></html>",
    ]

    html_body = "\n".join(html_parts)

    # Plain text fallback
    plain_body = (
        f"[EXPRESS OPS] GDC->MOPS1 Transfer Request - {today_str}\n\n"
        f"Please see the HTML version of this email for the transfer details.\n\n"
        f"Total: {total_lines} line(s) across {len(by_container)} container(s).\n\n"
        f"Regards,\nExpress OPS Automation (gdc_transfer_check)"
    )

    if dry_run:
        print(f"\n[DRY-RUN] Email to : {to_addr}")
        print(f"          Subject  : {subject}")
        print(f"          Lines    : {total_lines} across {len(by_container)} containers")
        print(f"          Format   : HTML")
        # Print a readable text preview
        for ckey, items in by_container.items():
            deduped = {
                i["pn"]: i for i in items
                if i["shortfall"] > 0
            }
            if not deduped:
                continue
            all_articles = ", ".join(dict.fromkeys(i["article"] for i in items))
            print(f"\n  {ckey} | {all_articles}")
            for item in deduped.values():
                print(f"    {item['pn']:<16} {item.get('pn_desc','')[:30]:<32} "
                      f"{item['source_location']:<10} {int(item['shortfall']):>6}")
        return

    try:
        import win32com.client
        outlook  = win32com.client.Dispatch("Outlook.Application")
        mail     = outlook.CreateItem(0)
        mail.To         = to_addr
        mail.Subject    = subject
        mail.HTMLBody   = html_body
        mail.Importance = 2   # olImportanceHigh
        mail.Send()
        print(f"  [OK] Email sent to {to_addr}")
    except ImportError:
        print("  [ERROR] win32com not available — pip install pywin32")
    except Exception as e:
        print(f"  [ERROR] Email send failed: {e}")

# ── Confluence ────────────────────────────────────────────────────────────────
def esc(v):
    return html_lib.escape(str(v), quote=True) if v not in (None, "") else ""

def make_confluence_session(cfg):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {cfg['confluence']['pat']}",
        "Content-Type": "application/json",
        "Accept":       "application/json"
    })
    s.verify = False
    return s

STATUS_BADGE = {
    "MO Raised":          ("Green",  "MO Raised"),
    "Transfer Triggered": ("Yellow", "Transfer Triggered"),
    "No GDC Stock":       ("Red",    "No GDC Stock"),
    "MOPS1 Ready":        ("Green",  "MOPS1 Ready"),
    "Template Missing":   ("Blue",   "Template Missing"),
    "No Article":         ("Grey",   "No Article"),
    "Error":              ("Grey",   "Error"),
}

def badge(status):
    colour, text = STATUS_BADGE.get(status, ("Grey", status))
    return (
        f'<ac:structured-macro ac:name="status">'
        f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        f'<ac:parameter ac:name="title">{esc(text)}</ac:parameter>'
        f'</ac:structured-macro>'
    )

def th(v):
    return (
        f'<th style="text-align:left;background-color:#f4f5f7;">'
        f'<strong>{esc(v)}</strong></th>'
    )

def td(v, bold=False):
    text = f"<strong>{esc(v)}</strong>" if bold else esc(v) or "&nbsp;"
    return f'<td style="vertical-align:top;padding:4px 8px;">{text}</td>'

def publish_confluence(cfg, session, results, dry_run=False):
    base       = cfg["confluence"]["base_url"]
    parent_id  = str(cfg["gdc_transfer_check"]["confluence_page_parent"])
    page_title = "GDC Transfer Check \u2014 Status"

    rows_html = ""
    for r in sorted(results, key=lambda x: x["key"]):
        rows_html += (
            "<tr>"
            + td(r["key"], bold=True)
            + td(r.get("articles", "\u2014"))
            + f'<td style="vertical-align:top;padding:4px 8px;">{badge(r["status"])}</td>'
            + td(str(r.get("transfer_count", 0)))
            + td(str(r.get("flagged_count", 0)))
            + td(r.get("last_checked", ""))
            + "</tr>\n"
        )

    updated_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    page_html  = (
        f"<p><em>Last updated: {esc(updated_ts)}</em></p>\n"
        "<table><tbody>\n<tr>"
        + th("Container") + th("Article(s)") + th("Status")
        + th("Transfer Lines") + th("Locked Elsewhere") + th("Last Checked")
        + "</tr>\n"
        + rows_html
        + "</tbody></table>"
    )

    if dry_run:
        print(f"\n[DRY-RUN] Would publish Confluence: {page_title} ({len(results)} rows)")
        return

    # Use existing page ID from config if available, else search by title
    page_id = str(cfg["gdc_transfer_check"].get("confluence_page_id", ""))

    if not page_id:
        search_r = session.get(
            f"{base}/rest/api/content",
            params={"title": page_title, "spaceKey": "EUDEMHTM0021",
                    "expand": "version"}
        )
        items = search_r.json().get("results", [])
        if items:
            page_id = items[0]["id"]

    if page_id:
        # Fetch current version
        ver_r = session.get(f"{base}/rest/api/content/{page_id}",
                            params={"expand": "version"})
        ver_r.raise_for_status()
        ver = ver_r.json()["version"]["number"] + 1
        payload = {
            "version": {"number": ver},
            "title":   page_title,
            "type":    "page",
            "body":    {"storage": {"value": page_html, "representation": "storage"}}
        }
        r = session.put(f"{base}/rest/api/content/{page_id}", json=payload)
        if r.status_code in (200, 204):
            print(f"  [OK] Confluence updated (page {page_id})")
        else:
            print(f"  [WARN] Confluence update failed: {r.status_code} — {r.text[:200]}")
    else:
        payload = {
            "type":      "page",
            "title":     page_title,
            "space":     {"key": "EUDEMHTM0021"},
            "ancestors": [{"id": parent_id}],
            "body":      {"storage": {"value": page_html, "representation": "storage"}}
        }
        r = session.post(f"{base}/rest/api/content", json=payload)
        if r.status_code == 200:
            print(f"  [OK] Confluence page created")
        else:
            print(f"  [WARN] Confluence create failed: {r.status_code} — {r.text[:200]}")

# ── Per-container processor ───────────────────────────────────────────────────
def process_container(container, jira_session, jira_base,
                       odbc_conn, cooldown_days, warehouse, do_write):
    key  = container["key"]
    desc = (container.get("renderedFields", {}).get("description")
            or container["fields"].get("description") or "")

    print(f"\n  [{key}]", end="", flush=True)

    # Fetch comments + reporter
    try:
        issue    = get_issue(jira_session, jira_base, key,
                             ["comment", "reporter"])
        comments = issue["fields"]["comment"]["comments"]
        reporter = issue["fields"].get("reporter") or {}
        reporter_name = reporter.get("name", "")
    except Exception as e:
        print(f" — comment fetch failed: {e}")
        return _result(key, status="Error"), [], []

    # ── Stop: MO already raised ──────────────────────────────────────────────
    if has_mo_number(comments):
        print(" → MO raised, done")
        return _result(key, status="MO Raised"), [], []

    # ── Parse NPI table ───────────────────────────────────────────────────────
    npi_rows = parse_npi_table(desc)
    today_str = str(date.today())

    if not npi_rows:
        print(" → NPI table missing")
        pending_comments = []
        notice_date = get_template_notice_date(comments)

        if notice_date is None:
            body = (
                f"[~{reporter_name}] \u2014 *gdc_transfer_check* could not find the "
                f"*NPI Built Type & Quantities* table in this container's description.\n\n"
                f"Please update the description using the correct template so that "
                f"material readiness checks can proceed. Without the table the "
                f"required build quantity cannot be determined.\n\n"
                f"#GDC-TEMPLATE-MISSING: Notified={today_str}#"
            )
            pending_comments.append((key, body))
            print("   posting first notice")
        elif working_days_since(notice_date) >= cooldown_days:
            body = (
                f"[~{reporter_name}] \u2014 *Reminder* ({today_str}): "
                f"The *NPI Built Type & Quantities* table is still missing. "
                f"Material readiness checks are paused until this is fixed.\n\n"
                f"#GDC-TEMPLATE-MISSING: Notified={today_str}#"
            )
            pending_comments.append((key, body))
            print("   posting reminder")
        else:
            print(f"   notice active since {notice_date}, cooldown not expired")

        return _result(key, status="Template Missing"), [], pending_comments

    # ── Process each NPI article ──────────────────────────────────────────────
    cooldown_state   = get_cooldown_state(comments, cooldown_days)
    all_triggers     = get_all_trigger_dates(comments)
    transfer_items   = []
    pending_comments = []
    any_shortfall    = False
    any_triggered    = False
    articles_seen    = []

    for npi_row in npi_rows:
        article     = npi_row["pn"]
        request_qty = npi_row["request_qty"]

        if article not in articles_seen:
            articles_seen.append(article)

        print(f"\n    {article} × {request_qty}", end="", flush=True)

        # BOM
        try:
            bom = fetch_bom(odbc_conn, article, warehouse)
        except Exception as e:
            print(f" — BOM failed: {e}")
            continue

        if not bom:
            print(" — no BOM, skipping")
            continue

        print(f" ({len(bom)} components)")

        for comp_pn, bom_qty in bom.items():
            required = bom_qty * request_qty

            # Cooldown active?
            if comp_pn in cooldown_state:
                any_shortfall = True
                continue

            # Stock check
            try:
                stock = fetch_stock(odbc_conn, comp_pn, warehouse)
            except Exception as e:
                print(f"      [WARN] stock fetch failed for {comp_pn}: {e}")
                continue

            mops1_avail = sum(
                qty for loc, qty in stock.items()
                if loc in ACCEPTABLE_LOCS
            )

            if mops1_avail >= required:
                continue  # sufficient — no action

            any_shortfall = True
            shortfall     = required - mops1_avail
            gdc_avail     = stock.get(PRIMARY_GDC, 0)

            # Flag other non-requestable GDC locs
            flagged = [
                f"{loc}({int(stock[loc])})"
                for loc in OTHER_GDC_LOCS
                if stock.get(loc, 0) > 0
            ]

            pn_desc = fetch_item_desc(odbc_conn, comp_pn)

            if gdc_avail <= 0:
                # GDC empty — add as flag-only row if other locs exist
                if flagged:
                    print(f"      {comp_pn}: GDC empty, flagged={flagged}")
                    transfer_items.append({
                        "container_key":   key,
                        "article":         article,
                        "pn":              comp_pn,
                        "pn_desc":         pn_desc,
                        "shortfall":       shortfall,
                        "source_location": "NOT IN GDC",
                        "flagged_locs":    flagged,
                        "prev_trigger":    all_triggers.get(comp_pn),
                    })
                else:
                    print(f"      {comp_pn}: shortfall={int(shortfall)}, "
                          f"no stock anywhere in GDC")
                continue

            if shortfall <= 0:
                continue

            print(f"      {comp_pn}: MOPS1={int(mops1_avail)}, "
                  f"need={int(required)}, shortfall={int(shortfall)}, "
                  f"GDC={int(gdc_avail)}"
                  + (f", flagged={flagged}" if flagged else ""))

            transfer_items.append({
                "container_key":   key,
                "article":         article,
                "pn":              comp_pn,
                "pn_desc":         pn_desc,
                "shortfall":       shortfall,
                "source_location": PRIMARY_GDC,
                "flagged_locs":    flagged,
                "prev_trigger":    all_triggers.get(comp_pn),
            })
            any_triggered = True

            # Cooldown comment
            if do_write:
                comment_body = (
                    f"#GDC-TRANSFER: PN={comp_pn}, "
                    f"Qty={int(shortfall)}, From={PRIMARY_GDC}, "
                    f"Triggered={today_str}#\n"
                    f"[Auto] Transfer requested: {int(shortfall)} pcs of "
                    f"*{comp_pn}* from {PRIMARY_GDC} \u2192 MOPS1. "
                    f"Next check in {cooldown_days} working day(s)."
                )
                pending_comments.append((key, comment_body))

    # Status
    if any_triggered:
        status = "Transfer Triggered"
    elif any_shortfall:
        status = "No GDC Stock"
    elif npi_rows:
        status = "MOPS1 Ready"
    else:
        status = "No Article"

    articles_str = ", ".join(articles_seen)

    requestable_count = sum(1 for t in transfer_items if t["source_location"] == PRIMARY_GDC)
    flagged_count     = sum(1 for t in transfer_items if t["source_location"] != PRIMARY_GDC)
    return (
        _result(key, status=status, articles=articles_str,
                transfer_count=requestable_count,
                flagged_count=flagged_count,
                last_checked=today_str),
        transfer_items,
        pending_comments
    )

def _result(key, status="Error", articles="—",
            transfer_count=0, flagged_count=0, last_checked=None):
    return {
        "key":           key,
        "articles":      articles,
        "status":        status,
        "transfer_count": transfer_count,
        "flagged_count":  flagged_count,
        "last_checked":  last_checked or str(date.today())
    }

# ── Entry point ───────────────────────────────────────────────────────────────
def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: gdc_transfer_check.py <scan|send> [--dry-run]")
        sys.exit(1)

    subcommand = argv[0].lower()
    dry_run    = "--dry-run" in argv

    if subcommand not in ("scan", "send"):
        print(f"Unknown subcommand: {subcommand!r}. Use 'scan' or 'send'.")
        sys.exit(1)

    do_write = (subcommand == "send") and not dry_run

    print("=" * 64)
    print(f"  gdc_transfer_check — {subcommand.upper()}"
          + (" [DRY-RUN]" if dry_run else ""))
    print("=" * 64)

    cfg      = load_config()
    gcfg     = cfg.get("gdc_transfer_check", {})
    warehouse = gcfg.get("warehouse_code", "MF1")
    cooldown  = int(gcfg.get("cooldown_working_days", 2))
    email_to  = gcfg.get("warehouse_email", "")

    jira_base    = cfg["jira"]["base_url"]
    jira_session = make_jira_session(cfg)
    conf_session = make_confluence_session(cfg)

    # ODBC
    try:
        odbc_conn = pyodbc.connect(f"DSN={cfg['m3']['dsn']}")
        print("  M3 ODBC connected")
    except Exception as e:
        print(f"  FATAL: ODBC failed: {e}")
        sys.exit(1)

    # Fetch containers
    print("\n  Fetching containers...")
    try:
        containers = search_all(
            jira_session, jira_base, JQL,
            ["key", "summary", "description", "reporter"],
            expand="renderedFields"
        )
        print(f"  {len(containers)} containers")
    except Exception as e:
        print(f"  FATAL: JIRA fetch failed: {e}")
        sys.exit(1)

    all_results   = []
    all_transfers = []
    all_comments  = []

    for container in containers:
        result, transfers, comments = process_container(
            container, jira_session, jira_base,
            odbc_conn, cooldown, warehouse, do_write
        )
        all_results.append(result)
        all_transfers.extend(transfers)
        all_comments.extend(comments)

    odbc_conn.close()

    # ── Send email ────────────────────────────────────────────────────────────
    requestable = [t for t in all_transfers
                   if t["source_location"] == PRIMARY_GDC]
    if subcommand == "send":
        if requestable:
            print(f"\n  Sending email ({len(requestable)} transfer lines)...")
            send_transfer_email(email_to, requestable, dry_run=dry_run)
        else:
            print("\n  No transfers to request today.")

    # ── Post JIRA comments ────────────────────────────────────────────────────
    if all_comments and do_write:
        print(f"\n  Posting {len(all_comments)} JIRA comment(s)...")
        for key, body in all_comments:
            post_comment(jira_session, jira_base, key, body, dry_run=dry_run)
    elif all_comments:
        print(f"\n  {len(all_comments)} comment(s) pending (scan mode — not posted)")

    # ── Confluence ────────────────────────────────────────────────────────────
    print("\n  Publishing to Confluence...")
    publish_confluence(cfg, conf_session, all_results, dry_run=dry_run)

    # ── Summary ───────────────────────────────────────────────────────────────
    statuses = {}
    for r in all_results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1

    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    for s, n in sorted(statuses.items()):
        print(f"  {s:<26} {n:>3}")
    print(f"  {'Transfer lines (requestable)':<26} {len(requestable):>3}")
    print(f"  {'Transfer lines (flag only)':<26} "
          f"{len(all_transfers)-len(requestable):>3}")
    print(f"  {'JIRA comments posted':<26} "
          f"{len(all_comments) if do_write else 0:>3}"
          + (" (dry-run, none posted)" if dry_run and all_comments else ""))
    print("=" * 64)


if __name__ == "__main__":
    main()
