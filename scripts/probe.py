"""
PROBE — KPI parity + overlay-sufficiency check (READ-ONLY, runs on company laptop).

Two questions this probe answers:

  GOAL 1 (parity): dump the numbers from BOTH sources so we can compare them —
    (a) the Confluence "2026 KPI Dashboard" page (550637258) that the user
        maintains by hand, and
    (b) the Tableau "ExpressOps KPIs" workbook (#3651) views.
    We print both fully; the actual number-matching is done back on the VPS.

  GOAL 2 (single source of truth): for every Tableau view, report whether it
    exposes PER-ROW identity — a wc_issue_key / wp_issue_key column carrying
    real JIRA keys (not the aggregated "*") next to the target-hit verdict.
    If yes, the KPI overlay can read red/green straight from Tableau instead of
    computing it from JIRA itself.

All calls are GET, plus Tableau auth/signin+signout and an optional VDS
read-metadata (all read-only; permitted by scripts/readonly_guard.py).
Nothing is written to any live system.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import yaml  # noqa: E402
except ImportError:
    print("ERROR: pyyaml not installed")
    raise

CONFIG_PATH = ROOT / "config" / "config.yaml"

# --- targets -------------------------------------------------------------
KPI_PAGE_ID = "550637258"          # Confluence "2026 KPI Dashboard" (space EUDEMHTM0815)
WORKBOOK_REPO_ID = "3651"          # Tableau "ExpressOps KPIs"
MAX_ROWS_PER_VIEW = 150

ISSUE_KEY_RE = re.compile(r"issue[_\s]?key", re.IGNORECASE)
TARGET_HIT_RE = re.compile(r"target[_\s]?hit", re.IGNORECASE)
# A column is an aggregated MEASURE (a number), not the raw key dimension, when
# its caption carries an aggregation word. "Distinct count of wc_issue_key" is a
# count — NOT the JIRA key. Only a bare dimension (e.g. "wc_issue_key") is real
# per-row identity the overlay can join on.
AGG_RE = re.compile(r"(count|%|\bavg\b|average|\bmin\b|\bmax\b|\bsum\b|median|attr|\bof\b)",
                    re.IGNORECASE)


def hr(char="=", n=78):
    print(char * n)


def load_cfg() -> dict:
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.yaml not found at {CONFIG_PATH}")
        sys.exit(1)
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


# ======================================================================
#  GOAL 1a — Confluence "2026 KPI Dashboard"
# ======================================================================
def dump_confluence(cfg: dict) -> None:
    hr()
    print("GOAL 1a — CONFLUENCE '2026 KPI Dashboard'  page", KPI_PAGE_ID)
    hr()

    ccfg = cfg.get("confluence") or {}
    base = (ccfg.get("base_url") or "").rstrip("/")
    pat = ccfg.get("pat") or ccfg.get("token")
    if not base or not pat:
        print("  ERROR: confluence.base_url / confluence.pat missing in config.yaml")
        return

    s = requests.Session()
    s.verify = bool(ccfg.get("verify_ssl", False))
    s.headers.update({"Authorization": f"Bearer {pat}", "Accept": "application/json"})

    url = f"{base}/rest/api/content/{KPI_PAGE_ID}"
    try:
        r = s.get(url, params={"expand": "body.storage,version,space,history.lastUpdated"}, timeout=40)
    except Exception as exc:  # noqa: BLE001
        print(f"  CONNECTION ERROR: {exc!r}")
        return
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  BODY: {r.text[:600]}")
        return

    j = r.json()
    space = (j.get("space") or {}).get("key", "?")
    title = j.get("title", "?")
    ver = (j.get("version") or {}).get("number", "?")
    when = (j.get("version") or {}).get("when", "?")
    print(f"  space={space}  title={title!r}  version={ver}  last_updated={when}")

    html = (((j.get("body") or {}).get("storage") or {}).get("value")) or ""
    print(f"  storage bytes: {len(html)}")
    print()
    _dump_html_tables(html)


def _dump_html_tables(html: str) -> None:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  (bs4 not installed — raw table cells via regex fallback)")
        _dump_html_tables_regex(html)
        return

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        print("  No <table> found. First 1500 chars of storage body:")
        print("  " + soup.get_text(" ", strip=True)[:1500])
        return

    print(f"  {len(tables)} table(s) found.\n")
    for ti, tbl in enumerate(tables, 1):
        print(f"  --- TABLE {ti} ---")
        for tr in tbl.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            row = [c.get_text(" ", strip=True) for c in cells]
            if any(row):
                print("    " + " | ".join(row))
        print()


def _dump_html_tables_regex(html: str) -> None:
    tables = re.findall(r"<table.*?</table>", html, re.IGNORECASE | re.DOTALL)
    print(f"  {len(tables)} table(s) found (regex).\n")
    for ti, tbl in enumerate(tables, 1):
        print(f"  --- TABLE {ti} ---")
        for tr in re.findall(r"<tr.*?</tr>", tbl, re.IGNORECASE | re.DOTALL):
            cells = re.findall(r"<t[hd].*?</t[hd]>", tr, re.IGNORECASE | re.DOTALL)
            row = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in cells]
            if any(row):
                print("    " + " | ".join(row))
        print()


# ======================================================================
#  GOAL 1b + GOAL 2 — Tableau "ExpressOps KPIs" (#3651)
# ======================================================================
def decode_csv(content: bytes) -> str:
    if content.startswith(b"\xff\xfe") or content.startswith(b"\xfe\xff"):
        return content.decode("utf-16")
    if b"\x00" in content[:64]:
        try:
            return content.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1", errors="replace")


def dump_tableau(cfg: dict) -> None:
    hr()
    print("GOAL 1b + GOAL 2 — TABLEAU 'ExpressOps KPIs'  workbook", WORKBOOK_REPO_ID)
    hr()

    tcfg = cfg.get("tableau") or {}
    base = (tcfg.get("base_url") or "").rstrip("/")
    api_v = str(tcfg.get("api_version", "3.25"))
    pat_name = tcfg.get("pat_name") or tcfg.get("token_name")
    pat_secret = tcfg.get("pat_secret") or tcfg.get("token_secret")
    content_url = tcfg.get("content_url", "")
    if not base or not pat_secret:
        print("  ERROR: tableau.base_url / tableau.pat_secret missing in config.yaml")
        return
    rest = f"{base}/api/{api_v}"

    s = requests.Session()
    s.verify = bool(tcfg.get("verify_ssl", False))
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    print(f"  base={base}  api=v{api_v}  token={pat_name}")
    print("\n[1] auth/signin ...")
    r = s.post(f"{rest}/auth/signin", data=json.dumps({"credentials": {
        "personalAccessTokenName": pat_name,
        "personalAccessTokenSecret": pat_secret,
        "site": {"contentUrl": content_url},
    }}), timeout=30)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  BODY: {r.text[:500]}")
        return
    cr = r.json()["credentials"]
    s.headers["X-Tableau-Auth"] = cr["token"]
    site_id = cr["site"]["id"]
    print(f"  OK site_id={site_id}")

    summary_rows = []
    try:
        wb_luid = _resolve_workbook(s, rest, site_id)
        if not wb_luid:
            return

        print("\n[3] workbook connections (data sources) ...")
        datasources = []  # (name, luid) discovered live — LUIDs change on republish
        r = s.get(f"{rest}/sites/{site_id}/workbooks/{wb_luid}/connections", timeout=60)
        if r.status_code == 200:
            conns = r.json().get("connections", {}).get("connection", [])
            if isinstance(conns, dict):
                conns = [conns]
            for c in conns:
                ds = c.get("datasource", {})
                print(f"  datasource={ds.get('name')!r} luid={ds.get('id')}")
                if ds.get("id"):
                    datasources.append((ds.get("name"), ds.get("id")))
        else:
            print(f"  HTTP {r.status_code}: {r.text[:300]}")

        print("\n[4] views ...")
        r = s.get(f"{rest}/sites/{site_id}/workbooks/{wb_luid}/views", timeout=60)
        views = []
        if r.status_code == 200:
            views = r.json().get("views", {}).get("view", [])
            if isinstance(views, dict):
                views = [views]
        else:
            print(f"  HTTP {r.status_code}: {r.text[:300]}")
        print(f"  {len(views)} view(s)")

        print("\n[5] per-view CSV export + per-row-identity analysis ...")
        for v in views:
            row = _dump_view(s, rest, site_id, v)
            if row:
                summary_rows.append(row)

        _vds_recheck(s, base, datasources)
    finally:
        try:
            s.post(f"{rest}/auth/signout", timeout=15)
            print("\n[signout] OK")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[signout] failed: {exc!r}")

    # --- GOAL 2 verdict table ---
    hr()
    print("GOAL 2 SUMMARY — can the overlay read per-container status from Tableau?")
    hr()
    print(f"  {'view':32} {'issue_key col':14} {'real keys':10} {'target_hit':11} verdict")
    for row in summary_rows:
        verdict = "PER-ROW OK" if (row["real_keys"] > 0 and row["has_target_hit"]) else "-"
        print(f"  {row['name'][:32]:32} {('yes' if row['has_key'] else 'no'):14} "
              f"{row['real_keys']:<10} {('yes' if row['has_target_hit'] else 'no'):11} {verdict}")
    print()
    if any(r["real_keys"] > 0 and r["has_target_hit"] for r in summary_rows):
        print("  => At least one view exposes per-container identity + verdict.")
        print("     Single source of truth for the overlay is REACHABLE from Tableau.")
    else:
        print("  => No view exposes real per-container keys next to a target-hit verdict.")
        print("     Overlay still needs either a detail sheet added to the workbook,")
        print("     or datasource (VDS) access — see GOAL-2 memo.")


def _resolve_workbook(s, rest, site_id):
    print("\n[2] locate workbook #{} ...".format(WORKBOOK_REPO_ID))
    workbooks = []
    page = 1
    while True:
        r = s.get(f"{rest}/sites/{site_id}/workbooks",
                  params={"pageSize": 1000, "pageNumber": page}, timeout=60)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text[:300]}")
            return None
        body = r.json()
        batch = body.get("workbooks", {}).get("workbook", [])
        if isinstance(batch, dict):
            batch = [batch]
        workbooks.extend(batch)
        pag = body.get("pagination", {})
        if len(workbooks) >= int(pag.get("totalAvailable", len(workbooks))) or not batch:
            break
        page += 1

    marker = f"/workbooks/{WORKBOOK_REPO_ID}"
    for wb in workbooks:
        if (wb.get("webpageUrl", "").rstrip("/").endswith(marker)
                or "expressops kpis" in wb.get("name", "").lower()):
            print(f"  FOUND luid={wb['id']} name={wb.get('name')!r} "
                  f"project={(wb.get('project') or {}).get('name')!r} "
                  f"updated={wb.get('updatedAt')}")
            return wb["id"]
    print(f"  workbook #{WORKBOOK_REPO_ID} not found among {len(workbooks)} visible workbooks")
    return None


def _dump_view(s, rest, site_id, v) -> dict | None:
    name = v.get("name", "?")
    luid = v.get("id")
    r = s.get(f"{rest}/sites/{site_id}/views/{luid}/data",
              headers={"Content-Type": None, "Accept": "*/*"}, timeout=120)
    if r.status_code != 200:
        print(f"\n  --- view {name!r}  HTTP {r.status_code} ---")
        print(f"      {r.text[:200]}")
        return {"name": name, "has_key": False, "real_keys": 0, "has_target_hit": False}

    text = decode_csv(r.content)
    lines = text.splitlines()
    if not lines:
        print(f"\n  --- view {name!r}  (empty) ---")
        return {"name": name, "has_key": False, "real_keys": 0, "has_target_hit": False}

    header = lines[0]
    delim = ";" if header.count(";") >= header.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    cols = rows[0] if rows else []
    data = rows[1:]

    # real identity = an issue_key column that is NOT an aggregation/measure.
    key_idxs = [i for i, c in enumerate(cols)
                if ISSUE_KEY_RE.search(c) and not AGG_RE.search(c)]
    agg_key_cols = [c for c in cols if ISSUE_KEY_RE.search(c) and AGG_RE.search(c)]
    hit_idxs = [i for i, c in enumerate(cols)
                if TARGET_HIT_RE.search(c) and not AGG_RE.search(c)]

    real_keys = set()
    star = 0
    for drow in data:
        for ki in key_idxs:
            if ki < len(drow):
                val = (drow[ki] or "").strip()
                if val == "*":
                    star += 1
                elif val:
                    real_keys.add(val)

    print(f"\n  --- view {name!r}  rows={len(data)}  cols={len(cols)}  "
          f"delim={delim!r}  bytes={len(r.content)} ---")
    print(f"      cols: {cols}")
    for drow in data[:MAX_ROWS_PER_VIEW]:
        print("      " + delim.join(drow))
    if len(data) > MAX_ROWS_PER_VIEW:
        print(f"      ... ({len(data) - MAX_ROWS_PER_VIEW} more rows)")
    if key_idxs:
        print(f"      >> RAW issue_key dimension(s): {[cols[i] for i in key_idxs]}  "
              f"real_keys={len(real_keys)}  star_rows={star}")
    if agg_key_cols:
        print(f"      >> (aggregated only — NOT per-row identity: {agg_key_cols})")
    if hit_idxs:
        print(f"      >> target_hit column(s): {[cols[i] for i in hit_idxs]}")

    return {
        "name": name,
        "has_key": bool(key_idxs),
        "real_keys": len(real_keys),
        "has_target_hit": bool(hit_idxs),
    }


def _vds_recheck(s, base, datasources) -> None:
    """Can we read RAW per-row data from the published datasources via VizQL Data
    Service? Tests the CURRENT LUIDs discovered from the workbook connections
    (they change whenever a datasource is republished, so hardcoding is wrong).
    A 200 on read-metadata means per-row access is open — true single source of
    truth for the overlay, no workbook-owner detail sheet needed."""
    print("\n[6] VDS read-metadata on CURRENT datasource LUIDs ...")
    if not datasources:
        print("  (no datasource LUIDs discovered — skipping)")
        return
    ep = f"{base}/api/v1/vizql-data-service/read-metadata"
    any_open = False
    for name, luid in datasources:
        payload = json.dumps({"datasource": {"datasourceLuid": luid}})
        try:
            r = s.post(ep, data=payload, timeout=25)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name} ({luid}) -> ERROR {exc!r}")
            continue
        print(f"  {name} ({luid}) -> HTTP {r.status_code}  {r.text[:220]}")
        if r.status_code == 200:
            any_open = True
    if any_open:
        print("  >> VDS ACCESS IS OPEN — raw per-row rows are queryable. The overlay")
        print("     can read wc_issue_key + per-phase verdicts straight from Tableau.")
    else:
        print("  >> VDS still not queryable (403=no permission / 400=bad request).")
        print("     Per-row identity would need an unaggregated detail sheet in the")
        print("     workbook, or datasource API-access granted by the owner.")


def main() -> None:
    cfg = load_cfg()
    dump_confluence(cfg)
    print()
    dump_tableau(cfg)


if __name__ == "__main__":
    main()
