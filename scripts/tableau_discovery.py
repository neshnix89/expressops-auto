"""
Tableau Server REST API discovery (run on company laptop via Relay).

Read-only discovery for the KPI integration:
  1. Auth signin with PAT (from config.yaml `tableau` section).
  2. List all workbooks (luid + name + project) so we can map repo id 3651.
  3. If a target workbook is given (luid or name substring via argv[1]),
     enumerate its views, connections (data sources), and try a CSV export
     of the first view.

Usage (on company laptop):
  python scripts/tableau_discovery.py
  python scripts/tableau_discovery.py <workbook-luid-or-name-substring>

No writes are performed against Tableau. Auth signin only creates a
short-lived session token, which is signed out at the end.
"""

import sys
import json
from pathlib import Path

import yaml

try:
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:  # pragma: no cover
    print("ERROR: requests/urllib3 not installed in this environment")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

XMLNS = {"t": "http://tableau.com/api"}


def line(char="-", n=70):
    print(char * n)


def load_tableau_cfg():
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.yaml not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tcfg = data.get("tableau")
    if not tcfg:
        print("ERROR: no `tableau` section in config.yaml.")
        print(f"Top-level config keys present: {sorted(data.keys())}")
        sys.exit(1)
    return tcfg


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None

    cfg = load_tableau_cfg()
    base = cfg["base_url"].rstrip("/")
    api_version = str(cfg.get("api_version", "3.25"))
    pat_name = cfg.get("pat_name") or cfg.get("token_name")
    pat_secret = cfg.get("pat_secret") or cfg.get("token_secret")
    content_url = cfg.get("content_url", "")
    verify = bool(cfg.get("verify_ssl", False))
    api = f"{base}/api/{api_version}"

    line("=")
    print(f"Tableau discovery  base={base}  api=v{api_version}  token={pat_name}")
    line("=")

    s = requests.Session()
    s.verify = verify
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    # --- 1. AUTH SIGNIN ---
    print("\n[1] POST auth/signin ...")
    payload = {
        "credentials": {
            "personalAccessTokenName": pat_name,
            "personalAccessTokenSecret": pat_secret,
            "site": {"contentUrl": content_url},
        }
    }
    try:
        r = s.post(f"{api}/auth/signin", data=json.dumps(payload), timeout=30)
    except Exception as exc:
        print(f"  CONNECTION ERROR: {exc!r}")
        sys.exit(1)

    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  BODY: {r.text[:1000]}")
        print("  AUTH FAILED — stopping.")
        sys.exit(1)

    creds = r.json()["credentials"]
    token = creds["token"]
    site_id = creds["site"]["id"]
    site_content = creds["site"].get("contentUrl", "")
    user_id = creds["user"]["id"]
    s.headers.update({"X-Tableau-Auth": token})
    print(f"  OK  site_id={site_id}  site_contentUrl='{site_content}'  user_id={user_id}")

    try:
        run_discovery(s, api, site_id, target)
    finally:
        # --- sign out (best effort) ---
        try:
            s.post(f"{api}/auth/signout", timeout=15)
            print("\n[signout] OK")
        except Exception as exc:
            print(f"\n[signout] failed: {exc!r}")


def run_discovery(s, api, site_id, target):
    # --- 2. LIST WORKBOOKS (paginated) ---
    print("\n[2] GET workbooks (all, paginated) ...")
    workbooks = []
    page = 1
    while True:
        r = s.get(
            f"{api}/sites/{site_id}/workbooks",
            params={"pageSize": 1000, "pageNumber": page},
            timeout=60,
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text[:800]}")
            break
        body = r.json()
        pag = body.get("pagination", {})
        batch = body.get("workbooks", {}).get("workbook", [])
        if isinstance(batch, dict):
            batch = [batch]
        workbooks.extend(batch)
        total = int(pag.get("totalAvailable", len(workbooks)))
        if len(workbooks) >= total or not batch:
            break
        page += 1

    print(f"  total workbooks visible to token: {len(workbooks)}")
    line()
    for wb in workbooks:
        proj = wb.get("project", {}).get("name", "?")
        print(
            f"  luid={wb.get('id')}  name={wb.get('name')!r}  "
            f"project={proj!r}  contentUrl={wb.get('contentUrl')!r}  "
            f"webpageUrl={wb.get('webpageUrl')!r}"
        )
    line()

    if not target:
        print(
            "\nNo target workbook arg given. Re-run with a luid or name substring "
            "to enumerate views/connections, e.g.:\n"
            "  python scripts/tableau_discovery.py <luid-or-name>"
        )
        return

    # --- resolve target ---
    # target may be: a luid, a name substring, or the numeric repository id
    # from the workbook URL (#/workbooks/<id>/views), which only appears in
    # webpageUrl, e.g. ".../workbooks/3651".
    tl = target.lower()
    repo_marker = f"/workbooks/{target}"
    matches = [
        wb for wb in workbooks
        if wb.get("id") == target
        or tl in (wb.get("name", "").lower())
        or (wb.get("webpageUrl", "").rstrip("/").endswith(repo_marker))
    ]
    if not matches:
        print(f"\nNo workbook matched target {target!r}.")
        return
    if len(matches) > 1:
        print(f"\nTarget {target!r} matched {len(matches)} workbooks; using first.")
    wb = matches[0]
    wb_luid = wb["id"]
    print(f"\n>>> Target workbook: luid={wb_luid} name={wb.get('name')!r}")

    # --- 3. VIEWS ---
    print("\n[3] GET workbook views ...")
    r = s.get(f"{api}/sites/{site_id}/workbooks/{wb_luid}/views", timeout=60)
    print(f"  HTTP {r.status_code}")
    views = []
    if r.status_code == 200:
        views = r.json().get("views", {}).get("view", [])
        if isinstance(views, dict):
            views = [views]
        for v in views:
            print(f"  view luid={v.get('id')}  name={v.get('name')!r}  "
                  f"contentUrl={v.get('contentUrl')!r}")
    else:
        print(f"  BODY: {r.text[:800]}")

    # --- 5. CONNECTIONS / DATA SOURCES ---
    print("\n[4] GET workbook connections (data sources) ...")
    r = s.get(f"{api}/sites/{site_id}/workbooks/{wb_luid}/connections", timeout=60)
    print(f"  HTTP {r.status_code}")
    if r.status_code == 200:
        conns = r.json().get("connections", {}).get("connection", [])
        if isinstance(conns, dict):
            conns = [conns]
        for c in conns:
            ds = c.get("datasource", {})
            print(f"  conn id={c.get('id')}  type={c.get('type')!r}  "
                  f"serverAddress={c.get('serverAddress')!r}  "
                  f"serverPort={c.get('serverPort')!r}  "
                  f"datasource={ds.get('name')!r} (luid={ds.get('id')})")
    else:
        print(f"  BODY: {r.text[:800]}")

    # --- 4. CSV EXPORT of first view ---
    print("\n[5] GET view/{id}/data (CSV export of first view) ...")
    if not views:
        print("  no views to export")
        return
    v0 = views[0]
    csv_url = f"{api}/sites/{site_id}/views/{v0['id']}/data"
    # The session defaults to JSON content-type/accept; the data endpoint
    # serves CSV and 406s on a too-specific Accept. Drop Content-Type and
    # use a permissive Accept (None values are not sent by requests).
    for accept in ("*/*", "text/csv", None):
        hdrs = {"Content-Type": None, "Accept": accept}
        r = s.get(csv_url, headers=hdrs, timeout=120)
        print(f"  view={v0.get('name')!r}  Accept={accept!r}  HTTP {r.status_code} "
              f"content-type={r.headers.get('Content-Type')}")
        if r.status_code == 200:
            text = r.text
            print(f"  CSV bytes={len(text)}  --- first 1500 chars ---")
            print(text[:1500])
            break
        else:
            print(f"  BODY: {r.text[:300]}")


if __name__ == "__main__":
    main()
