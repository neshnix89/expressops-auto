"""
Scan all Tableau views the PAT can see and find any that expose per-row
KPI data (a `wc_issue_key` or `wp_issue_key` column in their CSV header).

Strategy:
  1. Auth signin.
  2. GET /sites/{site}/views (paginated) -> every view visible to the PAT.
  3. Filter view + workbook names by KPI-ish keywords to keep the scan
     under a few hundred HTTP calls.
  4. For each filtered view, fetch the view's summary CSV header
     (Accept: */*, drop JSON content-type, semicolon-delimited).
  5. Print any view whose header contains an issue_key column, plus a
     summary of what was scanned.

Read-only. Per-CSV fetch returns at most a few KB so the total transfer
is small even across hundreds of views.
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
RESULTS_PATH = PROJECT_ROOT / "scripts" / "_perrow_scan_results.json"
MAX_WORKERS = 20
PER_REQ_TIMEOUT = 10  # seconds
CANDIDATE_CAP = 200   # keep scan well under the relay's ~95s kill
SAVE_EVERY = 25       # incremental save every N completed probes

NAME_KEYWORDS = re.compile(
    r"(npi|kpi|expressops|pcba|smt|"
    r"work[\s_]?container|work[\s_]?package|wc[_\s]|wp[_\s]|"
    r"detail|list|raw|underlying|drill|per[\s_-]?(container|package|wc|wp|issue))",
    re.IGNORECASE,
)
KEY_COL_RE = re.compile(r"(wc|wp)[_\s]?issue[_\s]?key|issue[_\s]?key", re.IGNORECASE)


def load_tableau_cfg():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    t = data.get("tableau")
    if not t:
        print("ERROR: no `tableau` section in config.yaml")
        sys.exit(1)
    return t


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


def main():
    cfg = load_tableau_cfg()
    base = cfg["base_url"].rstrip("/")
    api_v = str(cfg.get("api_version", "3.25"))
    rest = f"{base}/api/{api_v}"

    s = requests.Session()
    s.verify = bool(cfg.get("verify_ssl", False))
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    print("=" * 70)
    print(f"Per-row view scan against {base}")
    print("=" * 70)

    print("\n[1] auth signin ...")
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
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  BODY: {r.text[:500]}")
        sys.exit(1)
    cr = r.json()["credentials"]
    s.headers["X-Tableau-Auth"] = cr["token"]
    site_id = cr["site"]["id"]
    print(f"  OK site_id={site_id}")

    try:
        # --- list all views ---
        print("\n[2] GET sites/{site}/views ...")
        views: list[dict] = []
        page = 1
        while True:
            r = s.get(
                f"{rest}/sites/{site_id}/views",
                params={"pageSize": 1000, "pageNumber": page,
                        "includeUsageStatistics": "false"},
                timeout=60,
            )
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {r.text[:400]}")
                break
            body = r.json()
            batch = body.get("views", {}).get("view", [])
            if isinstance(batch, dict):
                batch = [batch]
            views.extend(batch)
            pag = body.get("pagination", {})
            if len(views) >= int(pag.get("totalAvailable", len(views))) or not batch:
                break
            page += 1
        print(f"  total views visible: {len(views)}")

        # --- filter candidates ---
        candidates = []
        for v in views:
            wb = (v.get("workbook") or {}).get("name") or ""
            name = v.get("name") or ""
            if NAME_KEYWORDS.search(name) or NAME_KEYWORDS.search(wb):
                candidates.append(v)
        print(f"  candidate views after keyword filter: {len(candidates)}")
        if len(candidates) > CANDIDATE_CAP:
            candidates = candidates[:CANDIDATE_CAP]
            print(f"  -> capped to {len(candidates)} to fit relay subprocess budget")

        # --- probe each candidate's CSV header (parallel) ---
        print(f"\n[3] probing CSV headers with {MAX_WORKERS} parallel workers ...")
        hits: list[dict] = []
        errors = 0
        empties = 0
        start = time.time()

        token = s.headers["X-Tableau-Auth"]
        verify_ssl = s.verify

        def probe(v: dict) -> dict | None:
            """Returns hit-dict on success, "EMPTY"/"ERROR" sentinels, or None on miss."""
            view_luid = v.get("id")
            csv_url = f"{rest}/sites/{site_id}/views/{view_luid}/data"
            try:
                rr = requests.get(
                    csv_url,
                    headers={"X-Tableau-Auth": token, "Accept": "*/*"},
                    verify=verify_ssl,
                    timeout=PER_REQ_TIMEOUT,
                )
            except Exception:  # noqa: BLE001
                return {"_marker": "ERROR"}
            if rr.status_code != 200:
                return {"_marker": "ERROR", "code": rr.status_code}
            text = decode_csv(rr.content)
            lines = text.splitlines()
            header = lines[0] if lines else ""
            if not header:
                return {"_marker": "EMPTY"}
            if not KEY_COL_RE.search(header):
                return None
            wb = (v.get("workbook") or {}).get("name") or ""
            return {
                "view_luid": view_luid,
                "view_name": v.get("name"),
                "workbook_name": wb,
                "rows": max(len(lines) - 1, 0),
                "bytes": len(rr.content),
                "header": header,
            }

        def save_progress(scanned: int, complete: bool):
            RESULTS_PATH.write_text(
                json.dumps({
                    "scanned": scanned,
                    "total_candidates": len(candidates),
                    "complete": complete,
                    "hits": hits,
                    "errors": errors,
                    "empties": empties,
                    "elapsed_s": round(time.time() - start, 1),
                }, indent=2),
                encoding="utf-8",
            )

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(probe, v): v for v in candidates}
            done = 0
            for fut in as_completed(futures):
                done += 1
                res = fut.result()
                if res is None:
                    pass
                elif res.get("_marker") == "EMPTY":
                    empties += 1
                elif res.get("_marker") == "ERROR":
                    errors += 1
                else:
                    hits.append(res)
                    print(f"  HIT [{done}/{len(candidates)}] "
                          f"wb={res['workbook_name']!r} view={res['view_name']!r} "
                          f"rows={res['rows']} cols={res['header'].count(';')+1}")
                if done % SAVE_EVERY == 0:
                    save_progress(done, complete=False)
                    print(f"  ... {done}/{len(candidates)} "
                          f"(hits={len(hits)}, empty={empties}, err={errors}, "
                          f"elapsed={time.time()-start:.0f}s) [saved]")
            save_progress(done, complete=True)
        print(f"  done {len(candidates)} in {time.time()-start:.0f}s")

        # --- report ---
        print("\n" + "=" * 70)
        print(f"SCAN COMPLETE  candidates={len(candidates)}  "
              f"hits={len(hits)}  errors={errors}  empty={empties}")
        print("=" * 70)
        print(f"\nResults persisted to {RESULTS_PATH}")

        if not hits:
            print("\nNo view exposes a *_issue_key column under the keyword filter.")
            print("Per-row data isn't reachable via existing views with this PAT.")
        else:
            print("\nViews exposing per-row issue keys:")
            for h in hits:
                print(f"\n  workbook : {h['workbook_name']!r}")
                print(f"  view     : {h['view_name']!r}  (luid {h['view_luid']})")
                print(f"  rows     : {h['rows']}  bytes: {h['bytes']}")
                print(f"  header   : {h['header'][:400]}"
                      + ("..." if len(h['header']) > 400 else ""))
    finally:
        try:
            s.post(f"{rest}/auth/signout", timeout=15)
            print("\n[signout] OK")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[signout] failed: {exc!r}")


if __name__ == "__main__":
    main()
