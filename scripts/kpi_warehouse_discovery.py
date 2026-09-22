"""
Find the route to the NPI KPI fact tables, and print their real schema.

Run this ON THE COMPANY LAPTOP, once, before switching the overlay over:

    python scripts\\kpi_warehouse_discovery.py
    python scripts\\kpi_warehouse_discovery.py --save-mock     # also capture fixtures

It answers the three questions that block the migration and cannot be answered
from outside the corporate network:

  1. WHICH ROUTE — is `sync_user` a Tableau Server login or a database account?
     Every route is tried in turn and the outcome of each is printed, including
     the ones that fail, because "Tableau answered but the DB did not" is itself
     the answer.
  2. WHERE THE DATABASE IS — when the Tableau route works, the published data
     sources are asked for their own connection details (server address, port,
     database, the account Tableau itself uses). That is how the ODBC route gets
     a host without another round-trip to the BI team.
  3. WHAT THE COLUMNS ARE CALLED — every column of every fact table, next to the
     logical field core/kpi_warehouse.py resolved it to, plus the columns nothing
     claimed. Anything unresolved is a line to paste into
     `kpi_warehouse.columns` in config.yaml.

Read-only throughout: SELECT, Tableau GET/signin/signout, and VDS
read-metadata / query-datasource. It never prints the password.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config                      # noqa: E402
from core.errors import FriendlyError, handle_friendly          # noqa: E402
from core.kpi_warehouse import (                                # noqa: E402
    DEFAULT_TABLES,
    FIELD_CANDIDATES,
    KpiWarehouseClient,
    OdbcDriver,
    TableauVdsDriver,
    WarehouseTable,
)

OUT_DIR = PROJECT_ROOT / "outputs" / "kpi_discovery"
MOCK_DIR = PROJECT_ROOT / "tasks" / "kpi_overlay" / "mock_data" / "warehouse"
TABLE_KEYS = ("wc", "wp", "combined")

_report_lines: list[str] = []


def say(line: str = "") -> None:
    print(line)
    _report_lines.append(line)


def head(title: str) -> None:
    say("")
    say("=" * 72)
    say(title)
    say("=" * 72)


def _err(exc: Exception) -> str:
    detail = getattr(exc, "message", None) or str(exc)
    hint = getattr(exc, "hint", None)
    return f"{detail}" + (f"  [{hint}]" if hint else "")


# ═══════════════════════════════════════════════════════════════
# 1 — config
# ═══════════════════════════════════════════════════════════════

def report_config(cfg: dict, tcfg: dict) -> None:
    head("1. CONFIG — what this machine is holding")
    say(f"  driver              : {cfg.get('driver') or 'auto'}")
    say(f"  user                : {cfg.get('user') or '(blank)'}")
    say(f"  password            : {'set' if cfg.get('password') else '(blank)'}")
    say(f"  tableau_auth        : {cfg.get('tableau_auth') or 'pat'}")
    say(f"  tableau.base_url    : {tcfg.get('base_url') or '(blank)'}")
    say(f"  tableau.pat_name    : {tcfg.get('pat_name') or '(blank)'}")
    say(f"  tableau.pat_secret  : {'set' if tcfg.get('pat_secret') else '(blank)'}")
    say(f"  dsn                 : {cfg.get('dsn') or '(blank)'}")
    say(f"  connection_string   : {'set' if cfg.get('connection_string') else '(blank)'}")
    say(f"  schema              : {cfg.get('schema') or '(blank)'}")
    tables = {**DEFAULT_TABLES, **(cfg.get("tables") or {})}
    for k in TABLE_KEYS:
        say(f"  table[{k}]{' ' * (12 - len(k))}: {tables.get(k)}")
    if not cfg.get("user") or not cfg.get("password"):
        say("")
        say("  >> kpi_warehouse.user / .password are blank in config/config.yaml.")
        say("     Put the sync_user credentials there before reading anything.")


# ═══════════════════════════════════════════════════════════════
# 2 — ODBC environment
# ═══════════════════════════════════════════════════════════════

def report_odbc_environment() -> None:
    head("2. ODBC — what this machine can even connect with")
    try:
        import pyodbc
    except ImportError:
        say("  pyodbc is NOT installed — run: pip install pyodbc")
        return
    try:
        drivers = pyodbc.drivers()
    except Exception as exc:  # noqa: BLE001
        say(f"  could not list drivers: {exc}")
        drivers = []
    say(f"  installed drivers ({len(drivers)}):")
    for d in drivers:
        say(f"    - {d}")
    try:
        sources = pyodbc.dataSources()
    except Exception as exc:  # noqa: BLE001
        say(f"  could not list DSNs: {exc}")
        sources = {}
    say(f"  configured DSNs ({len(sources)}):")
    for name, driver in sorted(sources.items()):
        say(f"    - {name}  ({driver})")
    if not sources:
        say("    (none — the ODBC route needs a DSN, or a full connection_string)")


# ═══════════════════════════════════════════════════════════════
# 2b — try the machine's own DSNs for the fact tables
# ═══════════════════════════════════════════════════════════════

FACT_NAMES_SQL = "', '".join(n.upper() for n in DEFAULT_TABLES.values())

# Exact names first; if the warehouse spells them differently, a fuzzy sweep
# for anything NPI-and-KPI-ish still finds them and prints the real spelling.
ORACLE_EXACT = f"""
SELECT owner, table_name, 'TABLE' AS kind FROM all_tables
 WHERE UPPER(table_name) IN ('{FACT_NAMES_SQL}')
UNION ALL
SELECT owner, view_name, 'VIEW' FROM all_views
 WHERE UPPER(view_name) IN ('{FACT_NAMES_SQL}')
"""
ORACLE_FUZZY = """
SELECT owner, table_name, 'TABLE' AS kind FROM all_tables
 WHERE UPPER(table_name) LIKE '%NPI%KPI%'
UNION ALL
SELECT owner, view_name, 'VIEW' FROM all_views
 WHERE UPPER(view_name) LIKE '%NPI%KPI%'
"""
GENERIC_EXACT = f"""
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES
 WHERE UPPER(TABLE_NAME) IN ('{FACT_NAMES_SQL}')
"""


def probe_dsns(cfg: dict, only: str | None = None, every_driver: bool = False) -> dict:
    """Log in to each candidate DSN with sync_user and look for the fact tables.

    The BI email gave credentials but no host. This machine already has 11 DSNs
    configured, two of them named DWH*, so the odds are high that the warehouse
    is already reachable from here and only needed pointing at.

    ONE login attempt per DSN, and the sweep stops dead on ORA-28000: a locked
    account is worse than an unanswered question, and hammering a wrong password
    across a list is how accounts get locked.
    """
    head("2b. DSN PROBE — is the warehouse already reachable from this machine?")
    found: dict = {"tried": [], "hits": {}}

    user = (cfg.get("user") or "").strip()
    password = (cfg.get("password") or "").strip()
    if not user or not password:
        say("  SKIPPED — kpi_warehouse.user / .password are blank (section 1).")
        return found

    try:
        import pyodbc
    except ImportError:
        say("  SKIPPED — pyodbc is not installed.")
        return found

    try:
        sources = pyodbc.dataSources()
    except Exception as exc:  # noqa: BLE001
        say(f"  could not list DSNs: {exc}")
        return found

    candidates = [(n, d) for n, d in sorted(sources.items())
                  if every_driver or "oracle" in d.lower()]
    if only:
        candidates = [(n, d) for n, d in candidates if n.lower() == only.lower()]
        if not candidates:
            say(f"  no DSN named '{only}' on this machine.")
            return found
    # A data warehouse is likelier to be called DWH* than anything else here.
    candidates.sort(key=lambda nd: (0 if "dwh" in nd[0].lower() else 1, nd[0]))

    if not candidates:
        say("  No Oracle DSN on this machine to try."
            + ("" if every_driver else " Use --all-drivers to try the rest."))
        return found

    say(f"  Trying {len(candidates)} DSN(s) as '{user}', ONE attempt each:")
    say("    " + ", ".join(n for n, _ in candidates))
    say("")
    say("  NOTE: a wrong password counts toward Oracle's failed-login limit on")
    say("  that database. This makes one attempt per DSN and stops immediately")
    say("  on ORA-28000 (account locked). Use --dsn NAME to test just one.")

    for name, driver in candidates:
        say("")
        say(f"  --- {name}  ({driver}) ---")
        found["tried"].append(name)
        try:
            conn = pyodbc.connect(f"DSN={name};UID={user};PWD={password};", timeout=15)
        except Exception as exc:  # noqa: BLE001 — every failure is a data point
            msg = str(exc).replace(password, "***")
            upper = msg.upper()
            if "ORA-28000" in upper or "ACCOUNT IS LOCKED" in upper:
                say(f"    ACCOUNT LOCKED — stopping the sweep now. {msg[:160]}")
                say("    Ask IT to unlock sync_user before running this again.")
                break
            if "ORA-01017" in upper:
                say("    login rejected (ORA-01017) — sync_user is not an account "
                    "on this database, or the password differs here")
                continue
            say(f"    cannot connect: {msg[:200]}")
            continue

        say("    LOGIN OK")
        try:
            cur = conn.cursor()
            rows = []
            for sql in (ORACLE_EXACT, GENERIC_EXACT):
                try:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    break
                except Exception:  # noqa: BLE001 — wrong dialect for this DSN
                    continue
            if rows:
                say(f"    *** FOUND {len(rows)} matching object(s):")
                for r in rows:
                    say(f"      {r[0]}.{r[1]}  ({r[2]})")
                found["hits"][name] = [f"{r[0]}.{r[1]}" for r in rows]
            else:
                say("    connected, but none of the three fact tables are visible.")
                try:
                    cur.execute(ORACLE_FUZZY)
                    fuzzy = cur.fetchall()
                except Exception:  # noqa: BLE001
                    fuzzy = []
                if fuzzy:
                    say(f"    but {len(fuzzy)} NPI/KPI-ish object(s) are — check the spelling:")
                    for r in fuzzy[:25]:
                        say(f"      {r[0]}.{r[1]}  ({r[2]})")
                    found["hits"][name] = [f"{r[0]}.{r[1]}" for r in fuzzy]
            cur.close()
        finally:
            conn.close()

        if found["hits"].get(name):
            say("")
            say(f"    >> This is the one. Put it in config.yaml:")
            owner = found['hits'][name][0].split('.')[0]
            say(f"         kpi_warehouse:")
            say(f"           driver: \"odbc\"")
            say(f"           dsn: \"{name}\"")
            say(f"           schema: \"{owner}\"")
            break

    if not found["hits"]:
        say("")
        say("  No DSN on this machine exposes the fact tables. Ask the BI team for")
        say("  the database host/port/service for sync_user, or for a DSN to be")
        say("  added — sections 1 and 4 of this report say exactly what was tried.")
    return found


# ═══════════════════════════════════════════════════════════════
# 3 — Tableau: data sources and where their data actually lives
# ═══════════════════════════════════════════════════════════════

def report_tableau_datasources(cfg: dict, tcfg: dict) -> dict:
    """List the published data sources and ask each for its DB connection."""
    head("3. TABLEAU — published data sources and their underlying connections")
    found: dict = {"datasources": [], "connections": {}}
    driver = TableauVdsDriver(cfg, tcfg)
    try:
        session = driver.session
    except Exception as exc:  # noqa: BLE001
        say(f"  signin FAILED: {_err(exc)}")
        say("  (an SSO-only Tableau site rejects name/password — use the PAT)")
        return found

    base = driver.base
    api_v = driver.api_v
    site_id = driver._site_id  # noqa: SLF001 — set during signin, no accessor
    say(f"  signin OK, site_id={site_id}")

    try:
        r = session.get(f"{base}/api/{api_v}/sites/{site_id}/datasources",
                        params={"pageSize": "1000"}, timeout=60)
        r.raise_for_status()
        payload = r.json().get("datasources", {}).get("datasource", [])
    except Exception as exc:  # noqa: BLE001
        say(f"  listing data sources FAILED: {_err(exc)}")
        driver.close()
        return found

    wanted = {v.lower() for v in DEFAULT_TABLES.values()}
    say(f"  {len(payload)} data source(s) visible; the three KPI ones:")
    for ds in payload:
        name = ds.get("name", "")
        if name.lower() not in wanted and "npi" not in name.lower():
            continue
        luid = ds.get("id")
        say(f"    - {name}")
        say(f"        luid    : {luid}")
        say(f"        type    : {ds.get('type')}   project: "
            f"{(ds.get('project') or {}).get('name')}")
        say(f"        updated : {ds.get('updatedAt')}")
        found["datasources"].append({"name": name, "luid": luid,
                                     "type": ds.get("type"),
                                     "updatedAt": ds.get("updatedAt")})
        # THE important call: where does this published data source get its data?
        try:
            cr = session.get(
                f"{base}/api/{api_v}/sites/{site_id}/datasources/{luid}/connections",
                timeout=60)
            cr.raise_for_status()
            conns = cr.json().get("connections", {}).get("connection", [])
        except Exception as exc:  # noqa: BLE001
            say(f"        connections: NOT READABLE ({_err(exc)})")
            continue
        found["connections"][name] = conns
        for c in conns:
            say(f"        connection: type={c.get('type')} "
                f"server={c.get('serverAddress')}:{c.get('serverPort')} "
                f"as user={c.get('userName')}")
            say("          ^ this is the database the ODBC route needs "
                "(host / port / account)")

    driver.close()
    return found


# ═══════════════════════════════════════════════════════════════
# 4 — try every route
# ═══════════════════════════════════════════════════════════════

def try_routes(cfg: dict, tcfg: dict) -> dict[str, object]:
    """Attempt each route against the WC table; return the ones that worked."""
    head("4. ROUTES — which one can actually read Fact_pm_npi_wc_kpi")
    working: dict[str, object] = {}
    candidates = [
        ("tableau_vds", lambda: TableauVdsDriver(cfg, tcfg)),
        ("odbc", lambda: OdbcDriver(cfg, direct=False)),
        ("odbc_direct", lambda: OdbcDriver(cfg, direct=True)),
    ]
    for name, make in candidates:
        say("")
        say(f"  --- {name} ---")
        try:
            driver = make()
            rows, columns = driver.fetch("wc", limit=1)
        except Exception as exc:  # noqa: BLE001 — the point is to report, not raise
            say(f"    FAILED: {_err(exc)}")
            continue
        say(f"    OK — {len(columns)} column(s), first fetch returned {len(rows)} row(s)")
        working[name] = driver
    if not working:
        say("")
        say("  >> No route worked. The two likeliest reasons:")
        say("     - sync_user is a DATABASE account and nobody has given us the")
        say("       host/port yet — section 3 above prints it if Tableau is readable.")
        say("     - the credentials are not in config/config.yaml (section 1).")
    return working


# ═══════════════════════════════════════════════════════════════
# 5 — schema of each fact table
# ═══════════════════════════════════════════════════════════════

def dump_tables(driver, cfg: dict, save_mock: bool, sample: int) -> dict:
    head(f"5. SCHEMA — every column of every fact table (via {driver.name})")
    captured: dict[str, dict] = {}

    for table_key in TABLE_KEYS:
        say("")
        say(f"  ── {table_key}  ({ {**DEFAULT_TABLES, **(cfg.get('tables') or {})}[table_key] })")
        try:
            rows, columns = driver.fetch(table_key)
        except Exception as exc:  # noqa: BLE001
            say(f"    NOT READABLE: {_err(exc)}")
            continue

        overrides = (cfg.get("columns") or {}).get(table_key) or {}
        table = WarehouseTable(table_key, rows, columns, overrides, source=driver.name)
        say(f"    rows: {len(rows)}    columns: {len(columns)}")

        say("    resolved logical fields:")
        for logical in sorted(table.mapping):
            say(f"      {logical:<16} -> {table.mapping[logical]}")
        missing = sorted(set(FIELD_CANDIDATES.get(table_key, {})) - set(table.mapping))
        if missing:
            say(f"    UNRESOLVED logical fields: {', '.join(missing)}")
        unmapped = table.unmapped()
        if unmapped:
            say(f"    columns nothing claimed ({len(unmapped)}):")
            for c in unmapped:
                say(f"      - {c}")

        if rows:
            say(f"    first {min(sample, len(rows))} row(s):")
            for row in rows[:sample]:
                say("      " + json.dumps(row, default=str)[:600])

        captured[table_key] = {"columns": columns, "rows": rows,
                               "mapping": table.mapping, "unmapped": unmapped}

        if save_mock:
            MOCK_DIR.mkdir(parents=True, exist_ok=True)
            path = MOCK_DIR / f"{table_key}.json"
            path.write_text(json.dumps(
                {"_comment": f"captured {datetime.now().isoformat()} via {driver.name}",
                 "table": {**DEFAULT_TABLES, **(cfg.get("tables") or {})}[table_key],
                 "columns": columns, "rows": rows}, indent=2, default=str),
                encoding="utf-8")
            say(f"    mock fixture written: {path}")

    return captured


# ═══════════════════════════════════════════════════════════════

def run(save_mock: bool, sample: int, try_dsns: bool = False,
        only_dsn: str | None = None, every_driver: bool = False) -> int:
    config = load_config(mode_override="live")
    cfg = config.get("kpi_warehouse", {}) or {}
    tcfg = config.get("tableau", {}) or {}

    say(f"KPI warehouse discovery — {datetime.now().isoformat()}")

    report_config(cfg, tcfg)
    report_odbc_environment()
    dsn_info = {}
    if try_dsns:
        dsn_info = probe_dsns(cfg, only=only_dsn, every_driver=every_driver)
    else:
        head("2b. DSN PROBE — skipped")
        say("  Pass --try-dsns to log in to this machine's own DSNs as sync_user")
        say("  and look for the fact tables there. Left off by default because it")
        say("  attempts a real login against each database.")
    tableau_info = report_tableau_datasources(cfg, tcfg)
    working = try_routes(cfg, tcfg)

    captured: dict = {}
    if working:
        # Prefer the order the client itself prefers, so what discovery reports
        # is what a real run will use.
        for name in KpiWarehouseClient.AUTO_ORDER:
            if name in working:
                captured = dump_tables(working[name], cfg, save_mock, sample)
                break

    head("NEXT STEPS")
    if not working:
        say("  1. Fill in kpi_warehouse.user / .password in config/config.yaml.")
        say("  2. If section 3 printed a `connection: ... server=HOST:PORT`, give")
        say("     that host to kpi_warehouse.connection_string (or ask IT for a")
        say("     DSN) and re-run — that is the database sync_user belongs to.")
        say("  3. Not yet tried: this machine's own DSNs. Run")
        say("       run_kpi_find_dsn.bat   (or --try-dsns)")
        say("     to log in to each Oracle DSN as sync_user and look for the")
        say("     fact tables. DWHSALES / DWHWIS are the obvious candidates.")
        say("  4. If that finds nothing, send sections 1, 2b and 4 to the BI team.")
    else:
        used = next(n for n in KpiWarehouseClient.AUTO_ORDER if n in working)
        say(f"  1. Set kpi_warehouse.driver: {used}  (stop paying for auto-probing)")
        say("  2. Paste any UNRESOLVED logical field from section 5 into")
        say("     kpi_warehouse.columns.<table> in config.yaml.")
        say("  3. Validate before switching:")
        say("       python scripts\\validate_kpi_vs_tableau.py --live")
        say("  4. When the diff is understood, set kpi_overlay.source: tableau")

    for driver in working.values():
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt = OUT_DIR / f"discovery_{stamp}.txt"
    txt.write_text("\n".join(_report_lines), encoding="utf-8")
    js = OUT_DIR / f"discovery_{stamp}.json"
    js.write_text(json.dumps({"tableau": tableau_info,
                              "dsn_probe": dsn_info,
                              "routes": {k: True for k in working},
                              "tables": captured}, indent=2, default=str),
                  encoding="utf-8")
    print()
    print(f"Report written: {txt}")
    print(f"Raw data      : {js}")
    print("Neither file contains the password. Both are safe to send to IT/BI.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Discover the KPI fact-table route + schema")
    p.add_argument("--save-mock", action="store_true",
                   help="also write tasks/kpi_overlay/mock_data/warehouse/*.json")
    p.add_argument("--sample", type=int, default=3,
                   help="rows to print per table (default 3)")
    p.add_argument("--try-dsns", action="store_true",
                   help="log in to this machine's DSNs as sync_user and look "
                        "for the fact tables (one attempt per DSN)")
    p.add_argument("--dsn", metavar="NAME",
                   help="with --try-dsns, test only this one DSN")
    p.add_argument("--all-drivers", action="store_true",
                   help="with --try-dsns, try non-Oracle DSNs too")
    args = p.parse_args()
    try:
        return run(args.save_mock, args.sample, try_dsns=args.try_dsns,
                   only_dsn=args.dsn, every_driver=args.all_drivers)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
