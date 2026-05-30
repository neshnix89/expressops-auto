"""
Probe Tableau VizQL Data Service against a published data source.

VizQL Data Service exposes JSON query endpoints for a published data source
(server 2024.2+). We want to know whether we can pull per-row KPI data
directly — i.e. one row per wc_issue_key / wp_issue_key — to drive the
Tampermonkey overlay.

Default target: fact_pm_npi_wc_kpi (luid 2c72b33f-dca7-4f80-85b3-41220c5bc355).
Pass a different luid as argv[1] to probe wp / combined data sources.

Read-only: auth signin -> read-metadata -> sample query -> signout.
Prints raw response bodies (truncated) so we can adapt to whatever the
server returns instead of guessing the schema.
"""

import json
import sys
from pathlib import Path

import yaml
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

DEFAULT_DS_LUID = "2c72b33f-dca7-4f80-85b3-41220c5bc355"  # fact_pm_npi_wc_kpi

# Field captions we expect / hope to find, based on the view-CSV peek.
PREFERRED_FIELDS = [
    "wc_issue_key", "wc_target_hit", "wc_duration_workdays",
    "wc_running_duration_workdays", "wc_npi_location", "wc_summary",
    "wc_parked_status", "project_number", "target_line",
]


def load_tableau_cfg():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    t = data.get("tableau")
    if not t:
        print("ERROR: no `tableau` section in config.yaml")
        sys.exit(1)
    return t


def short(obj, n=900):
    s = json.dumps(obj, indent=2, default=str) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[:n] + f"\n  ... (truncated, total {len(s)} chars)"


def main():
    ds_luid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DS_LUID

    cfg = load_tableau_cfg()
    base = cfg["base_url"].rstrip("/")
    api_v = str(cfg.get("api_version", "3.25"))
    rest_api = f"{base}/api/{api_v}"
    vds_api = f"{base}/api/v1/vizql-data-service"
    verify = bool(cfg.get("verify_ssl", False))

    s = requests.Session()
    s.verify = verify
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    print("=" * 70)
    print(f"VDS probe  base={base}  datasource_luid={ds_luid}")
    print("=" * 70)

    # --- Auth ---
    print(f"\n[1] POST auth/signin (REST v{api_v}) ...")
    r = s.post(
        f"{rest_api}/auth/signin",
        data=json.dumps({
            "credentials": {
                "personalAccessTokenName": cfg["pat_name"],
                "personalAccessTokenSecret": cfg["pat_secret"],
                "site": {"contentUrl": cfg.get("content_url", "")},
            }
        }),
        timeout=30,
    )
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  BODY: {r.text[:600]}")
        sys.exit(1)
    cr = r.json()["credentials"]
    s.headers["X-Tableau-Auth"] = cr["token"]
    print(f"  OK site_id={cr['site']['id']}")

    try:
        # --- read-metadata ---
        print(f"\n[2] POST vds/read-metadata ...")
        r = s.post(
            f"{vds_api}/read-metadata",
            data=json.dumps({"datasource": {"datasourceLuid": ds_luid}}),
            timeout=60,
        )
        print(f"  HTTP {r.status_code}  content-type={r.headers.get('Content-Type')}")
        captions: list[str] = []
        if r.status_code == 404:
            print("  BODY:", r.text[:500])
            print("  --> VizQL Data Service likely not enabled on this server.")
            return
        if r.status_code != 200:
            print(f"  BODY: {r.text[:800]}")
        else:
            body = r.json()
            print(f"  RAW BODY:\n{short(body, 1800)}")
            # Try common shapes for the field list.
            for path in (("data",), ("fields",), ("data", "fields")):
                cursor = body
                ok = True
                for key in path:
                    if isinstance(cursor, dict) and key in cursor:
                        cursor = cursor[key]
                    else:
                        ok = False
                        break
                if ok and isinstance(cursor, list):
                    for f in cursor:
                        if isinstance(f, dict):
                            cap = f.get("fieldCaption") or f.get("fieldName")
                            if cap:
                                captions.append(cap)
                    if captions:
                        break
            print(f"\n  --> extracted {len(captions)} field captions")
            for c in captions:
                print(f"      {c}")

        # --- query-datasource sample ---
        print(f"\n[3] POST vds/query-datasource (sample rows) ...")
        if captions:
            chosen = [c for c in PREFERRED_FIELDS if c in captions]
            if not chosen:
                chosen = captions[:6]
            print(f"  using fields: {chosen}")
        else:
            chosen = ["wc_issue_key", "wc_target_hit", "wc_duration_workdays"]
            print(f"  metadata gave no captions; guessing: {chosen}")
        payload = {
            "datasource": {"datasourceLuid": ds_luid},
            "query": {"fields": [{"fieldCaption": c} for c in chosen]},
        }
        r = s.post(
            f"{vds_api}/query-datasource",
            data=json.dumps(payload),
            timeout=120,
        )
        print(f"  HTTP {r.status_code}  content-type={r.headers.get('Content-Type')}")
        if r.status_code != 200:
            print(f"  BODY: {r.text[:1000]}")
        else:
            body = r.json()
            print(f"  RAW BODY:\n{short(body, 2400)}")
            # Try to surface first ~5 rows.
            rows = None
            if isinstance(body, dict):
                if isinstance(body.get("data"), list):
                    rows = body["data"]
                elif isinstance(body.get("rows"), list):
                    rows = body["rows"]
            elif isinstance(body, list):
                rows = body
            if rows is not None:
                print(f"\n  --> rows={len(rows)}  first 5:")
                for row in rows[:5]:
                    print(f"      {row}")
        # --- .tdsx download probe ---
        # If we have Download permission, we can read the .hyper extract locally
        # and bypass the VDS API permission entirely.
        print(f"\n[4] GET datasources/{{luid}}/content (.tdsx download probe) ...")
        r = s.get(
            f"{rest_api}/sites/{cr['site']['id']}/datasources/{ds_luid}/content",
            headers={"Content-Type": None, "Accept": "*/*"},
            timeout=60,
            stream=True,
        )
        print(f"  HTTP {r.status_code}  content-type={r.headers.get('Content-Type')}  "
              f"content-length={r.headers.get('Content-Length')}")
        if r.status_code == 200:
            head = r.raw.read(8) if hasattr(r, "raw") else r.content[:8]
            is_zip = head.startswith(b"PK\x03\x04")
            print(f"  first 8 bytes: {head!r}  zip-magic: {is_zip}")
            print("  --> Download permission granted. We can pull the .tdsx and "
                  "read the embedded .hyper extract per row.")
        else:
            print(f"  BODY: {r.text[:400]}")

        # --- list datasources (find any with friendlier perms) ---
        print(f"\n[5] GET sites/{{site}}/datasources (look for per-row friendly DS) ...")
        names_of_interest = ("fact_pm_npi", "wc_kpi", "wp_kpi", "npi")
        page = 1
        all_ds: list[dict] = []
        while True:
            r = s.get(
                f"{rest_api}/sites/{cr['site']['id']}/datasources",
                params={"pageSize": 1000, "pageNumber": page},
                timeout=60,
            )
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {r.text[:400]}")
                break
            body = r.json()
            batch = body.get("datasources", {}).get("datasource", [])
            if isinstance(batch, dict):
                batch = [batch]
            all_ds.extend(batch)
            pag = body.get("pagination", {})
            if len(all_ds) >= int(pag.get("totalAvailable", len(all_ds))) or not batch:
                break
            page += 1
        print(f"  total data sources visible: {len(all_ds)}")
        # Print ones with names that look like they cover NPI / WC / WP KPIs.
        matches = [
            d for d in all_ds
            if any(s in (d.get("name", "").lower()) for s in names_of_interest)
        ]
        print(f"  matching NPI/WC/WP keyword filter: {len(matches)}")
        for d in matches[:25]:
            proj = (d.get("project") or {}).get("name")
            print(f"    luid={d.get('id')}  name={d.get('name')!r}  "
                  f"project={proj!r}  type={d.get('type')!r}")
    finally:
        try:
            s.post(f"{rest_api}/auth/signout", timeout=15)
            print("\n[signout] OK")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[signout] failed: {exc!r}")


if __name__ == "__main__":
    main()
