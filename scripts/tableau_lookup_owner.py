"""
Look up the owner of the 'ExpressOps KPIs' workbook (luid
2614a7d6-ebde-4aba-93cd-def89d33fb39) and resolve to name + email.

Read-only: signin -> GET workbook -> GET user -> signout.
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

WORKBOOK_LUID = "2614a7d6-ebde-4aba-93cd-def89d33fb39"
DS_LUIDS = {
    "fact_pm_npi_wc_kpi": "2c72b33f-dca7-4f80-85b3-41220c5bc355",
    "fact_pm_npi_wp_kpi": "456b9a94-7d61-4dc3-98e7-05555c873f85",
    "fact_pm_npi_wc_wp_combined": "eb8a2c04-ca2c-4484-9f7c-1318b61542e7",
}


def load_cfg():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    t = data.get("tableau")
    if not t:
        print("ERROR: no `tableau` section in config.yaml")
        sys.exit(1)
    return t


def main():
    cfg = load_cfg()
    base = cfg["base_url"].rstrip("/")
    api_v = str(cfg.get("api_version", "3.25"))
    rest = f"{base}/api/{api_v}"

    s = requests.Session()
    s.verify = bool(cfg.get("verify_ssl", False))
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    r = s.post(
        f"{rest}/auth/signin",
        data=json.dumps({
            "credentials": {
                "personalAccessTokenName": cfg["pat_name"],
                "personalAccessTokenSecret": cfg["pat_secret"],
                "site": {"contentUrl": cfg.get("content_url", "")},
            }
        }),
        timeout=30,
    )
    if r.status_code != 200:
        print(f"signin HTTP {r.status_code}: {r.text[:400]}")
        sys.exit(1)
    cr = r.json()["credentials"]
    s.headers["X-Tableau-Auth"] = cr["token"]
    site_id = cr["site"]["id"]

    try:
        # workbook -> owner luid
        print(f"=== Workbook 'ExpressOps KPIs' (luid {WORKBOOK_LUID}) ===")
        r = s.get(f"{rest}/sites/{site_id}/workbooks/{WORKBOOK_LUID}", timeout=30)
        print(f"  GET /workbooks/{{luid}} HTTP {r.status_code}")
        if r.status_code == 200:
            wb = r.json().get("workbook", {})
            owner_luid = (wb.get("owner") or {}).get("id")
            print(f"  name        : {wb.get('name')!r}")
            print(f"  project     : {(wb.get('project') or {}).get('name')!r}")
            print(f"  owner luid  : {owner_luid}")
            print(f"  created     : {wb.get('createdAt')}")
            print(f"  updated     : {wb.get('updatedAt')}")
            if owner_luid:
                ru = s.get(f"{rest}/sites/{site_id}/users/{owner_luid}", timeout=30)
                print(f"\n  GET /users/{{owner_luid}} HTTP {ru.status_code}")
                if ru.status_code == 200:
                    u = ru.json().get("user", {})
                    print(f"  --- WORKBOOK OWNER ---")
                    print(f"  name        : {u.get('fullName') or u.get('name')!r}")
                    print(f"  login name  : {u.get('name')!r}")
                    print(f"  email       : {u.get('email')!r}")
                    print(f"  site role   : {u.get('siteRole')!r}")
                    print(f"  last login  : {u.get('lastLogin')}")
                else:
                    print(f"  BODY: {ru.text[:400]}")
        else:
            print(f"  BODY: {r.text[:400]}")

        # data sources owners (even though we can't see them by name, the workbook
        # GET above gave us a reference; let's try direct GETs anyway)
        print(f"\n=== Data source owners (probe) ===")
        for name, luid in DS_LUIDS.items():
            r = s.get(f"{rest}/sites/{site_id}/datasources/{luid}", timeout=30)
            print(f"  {name}  HTTP {r.status_code}")
            if r.status_code == 200:
                ds = r.json().get("datasource", {})
                owner_luid = (ds.get("owner") or {}).get("id")
                print(f"    owner luid: {owner_luid}")
                if owner_luid:
                    ru = s.get(f"{rest}/sites/{site_id}/users/{owner_luid}", timeout=30)
                    if ru.status_code == 200:
                        u = ru.json().get("user", {})
                        print(f"    owner name : {u.get('fullName') or u.get('name')!r}  "
                              f"email: {u.get('email')!r}")
    finally:
        try:
            s.post(f"{rest}/auth/signout", timeout=15)
        except Exception:
            pass


if __name__ == "__main__":
    main()
