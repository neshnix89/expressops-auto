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
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config                      # noqa: E402
from core.errors import FriendlyError, handle_friendly          # noqa: E402
from core.kpi_warehouse import (                                # noqa: E402
    DEFAULT_DATASOURCE_LUIDS,
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
    # The runners set PYTHONIOENCODING=utf-8, but run by hand on a cp1252
    # console the box-drawing characters raise UnicodeEncodeError mid-report
    # and take the whole discovery run with them. Degrade the CONSOLE line
    # only; the saved report is written as UTF-8 and keeps full fidelity.
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, "replace").decode(enc, "replace"))
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

def secret_sanity(loaded: str, section: str, key: str) -> list[str]:
    """Did the password in config.yaml survive YAML parsing intact?

    Never prints the password — only its length and character classes. The
    failure this exists to catch is silent and common: an UNQUOTED YAML scalar
    ends at a " #", so

        password: s3cr#t-value

    loads as "s3cr" and every login then fails with ORA-01017, which is
    indistinguishable from an account that does not exist. Four identical
    ORA-01017s across four unrelated databases is exactly the shape that bug
    makes, so it has to be ruled out before anyone emails the BI team.
    """
    import hashlib
    from core.config_loader import CONFIG_PATH
    notes: list[str] = []
    if not loaded:
        return notes

    # A fingerprint, not the secret. Lets two runs be compared: if this line is
    # identical after you swapped a token, the new one never reached the file.
    fp = hashlib.sha256(loaded.encode("utf-8")).hexdigest()[:8]
    classes = []
    if any(c.islower() for c in loaded):
        classes.append("lower")
    if any(c.isupper() for c in loaded):
        classes.append("upper")
    if any(c.isdigit() for c in loaded):
        classes.append("digit")
    if any(not c.isalnum() for c in loaded):
        classes.append("symbol")
    notes.append(f"{len(loaded)} chars, contains: {', '.join(classes) or 'nothing?'}"
                 f"   fingerprint sha256:{fp}")
    if loaded != loaded.strip():
        notes.append("!! it has leading/trailing WHITESPACE - quote it in config.yaml")

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return notes

    block = re.search(rf"(?ms)^{section}:\s*\n(.*?)(?=^\S|\Z)", raw)
    if not block:
        return notes
    line = re.search(rf"(?m)^\s+{key}:[ 	]*(.*)$", block.group(1))
    if not line:
        return notes

    raw_val = line.group(1).rstrip()
    quoted = len(raw_val) >= 2 and raw_val[0] in "\"'" and raw_val[-1] == raw_val[0]
    inner = raw_val[1:-1] if quoted else raw_val

    if not quoted:
        notes.append("the value in config.yaml is UNQUOTED")
        risky = [c for c in "#:{}[],&*!|>%@`" if c in raw_val]
        if risky:
            notes.append("!! it contains " + " ".join(repr(c) for c in risky)
                         + " - YAML can eat or misread these. Wrap it in single "
                           "quotes: password: '...'")
    if len(inner) != len(loaded):
        notes.append(f"!! the file holds {len(inner)} characters but YAML loaded "
                     f"{len(loaded)} - THE PASSWORD IS BEING TRUNCATED. Wrap it "
                     f"in single quotes.")
    return notes


def report_config(cfg: dict, tcfg: dict) -> None:
    head("1. CONFIG — what this machine is holding")
    say(f"  driver              : {cfg.get('driver') or 'auto'}")
    say(f"  user                : {cfg.get('user') or '(blank)'}")
    say(f"  password            : {'set' if cfg.get('password') else '(blank)'}")
    for note in secret_sanity(str(cfg.get("password") or ""), "kpi_warehouse", "password"):
        say(f"                        {note}")
    say(f"  tableau_auth        : {cfg.get('tableau_auth') or 'pat'}")
    say(f"  tableau.base_url    : {tcfg.get('base_url') or '(blank)'}")
    say(f"  tableau.pat_name    : {tcfg.get('pat_name') or '(blank)'}")
    say(f"  tableau.pat_secret  : {'set' if tcfg.get('pat_secret') else '(blank)'}")
    for note in secret_sanity(str(tcfg.get("pat_secret") or ""), "tableau", "pat_secret"):
        say(f"                        {note}")
    say("                        NOTE: tableau.pat_name must match the token's")
    say("                        name in Tableau EXACTLY. A new token with a new")
    say("                        name and an unchanged pat_name here is a 401.")
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
    found: dict = {"tried": [], "hits": {}, "unreadable": []}

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
            # pyodbc + the Oracle ODBC driver mangle UTF-16 error text into
            # "returned a result with an exception set", destroying the ORA-
            # code. Flag it for the oracledb retry in 2c rather than guessing:
            # ORA-01017 and ORA-28000 demand opposite responses.
            if "ORA-" not in upper:
                found["unreadable"].append(name)
                say(f"    cannot connect, AND the error text is unreadable: {msg[:140]}")
                say("      -> section 2c re-asks this one via oracledb")
            else:
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
# 2c — tnsnames.ora: what each DSN actually points at
# ═══════════════════════════════════════════════════════════════
#
# Two problems this solves at once.
#
# First, the Oracle ODBC driver returns its error text as UTF-16 and pyodbc
# mis-decodes it, so a failed connect can come back as the useless
# "<class 'pyodbc.Error'> returned a result with an exception set" with the
# ORA- code destroyed. That is not a cosmetic issue: ORA-01017 (not an account
# here) and ORA-28000 (account LOCKED) need completely different responses, and
# an unreadable error hides which one happened. `oracledb` is pure Python, is
# already a dependency for EDM, and reports the code cleanly.
#
# Second, tnsnames.ora names the actual host, port and service behind every
# alias. That is exactly the "where is the database" fact the BI email left
# out, and reading it needs no credentials at all.

TNS_ENTRY_RE = re.compile(r"([A-Za-z0-9_.\-]+(?:\s*,\s*[A-Za-z0-9_.\-]+)*)\s*=\s*\(")


def parse_tnsnames(text: str) -> dict[str, dict]:
    """alias -> {host, port, service}. Paren-depth scan, so nested ADDRESS
    blocks are consumed with their parent instead of being read as aliases."""
    text = re.sub(r"#[^\n]*", "", text)
    out: dict[str, dict] = {}
    i, n = 0, len(text)
    while i < n:
        m = TNS_ENTRY_RE.search(text, i)
        if not m:
            break
        depth, j = 0, m.end() - 1
        start = j
        while j < n:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = text[start:j + 1]
        host = re.search(r"HOST\s*=\s*([^)\s]+)", block, re.I)
        port = re.search(r"PORT\s*=\s*([^)\s]+)", block, re.I)
        svc = re.search(r"SERVICE_NAME\s*=\s*([^)\s]+)", block, re.I)
        sid = re.search(r"\bSID\s*=\s*([^)\s]+)", block, re.I)
        for name in (x.strip().upper() for x in m.group(1).split(",")):
            out[name] = {
                "host": host.group(1) if host else None,
                "port": port.group(1) if port else None,
                "service": svc.group(1) if svc else (sid.group(1) if sid else None),
                "is_sid": svc is None and sid is not None,
            }
        i = j + 1
    return out


def find_tnsnames() -> list[Path]:
    """Every tnsnames.ora this machine might be using, most authoritative first."""
    import os
    seen, out = set(), []

    def add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        if rp.is_file() and str(rp).lower() not in seen:
            seen.add(str(rp).lower())
            out.append(rp)

    if os.environ.get("TNS_ADMIN"):
        add(Path(os.environ["TNS_ADMIN"]) / "tnsnames.ora")
    if os.environ.get("ORACLE_HOME"):
        add(Path(os.environ["ORACLE_HOME"]) / "network" / "admin" / "tnsnames.ora")
    # Derive from the client on PATH — the usual way it is actually installed.
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if "oracle" not in entry.lower():
            continue
        p = Path(entry)
        add(p / "network" / "admin" / "tnsnames.ora")
        add(p.parent / "network" / "admin" / "tnsnames.ora")
    return out


def dsn_aliases() -> dict[str, str]:
    """DSN name -> the TNS alias it resolves through (its ServerName)."""
    out: dict[str, str] = {}
    try:
        import winreg
    except ImportError:
        return out
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            odbc = winreg.OpenKey(root, r"SOFTWARE\ODBC\ODBC.INI")
        except OSError:
            continue
        try:
            for idx in range(winreg.QueryInfoKey(odbc)[0]):
                try:
                    name = winreg.EnumKey(odbc, idx)
                    with winreg.OpenKey(odbc, name) as k:
                        server, _ = winreg.QueryValueEx(k, "ServerName")
                    if server:
                        out.setdefault(name, str(server))
                except OSError:
                    continue
        finally:
            odbc.Close()
    return out


def report_tns(unreadable: list[str], cfg: dict) -> dict:
    """Print where each DSN points, and re-ask the unreadable ones via oracledb."""
    head("2c. ORACLE TNS — where each DSN actually points")
    info: dict = {"files": [], "aliases": {}, "dsn_alias": {}, "retried": {}}

    files = find_tnsnames()
    info["files"] = [str(f) for f in files]
    if not files:
        say("  No tnsnames.ora found (TNS_ADMIN / ORACLE_HOME / PATH).")
    entries: dict[str, dict] = {}
    for f in files:
        say(f"  {f}")
        try:
            entries.update(parse_tnsnames(f.read_text(encoding="utf-8", errors="replace")))
        except OSError as exc:
            say(f"    unreadable: {exc}")
    info["aliases"] = entries

    mapping = dsn_aliases()
    info["dsn_alias"] = mapping
    if entries:
        say("")
        say(f"  {'DSN':<16} {'TNS ALIAS':<20} {'HOST':<34} {'PORT':<7} SERVICE")
        shown = set()
        for dsn, alias in sorted(mapping.items()):
            e = entries.get(alias.upper())
            if not e:
                continue
            shown.add(alias.upper())
            say(f"  {dsn:<16} {alias:<20} {str(e['host']):<34} "
                f"{str(e['port']):<7} {e['service']}")
        extra = [a for a in sorted(entries) if a not in shown]
        if extra:
            say("")
            say("  aliases in tnsnames.ora with no DSN pointing at them:")
            for a in extra:
                e = entries[a]
                say(f"    {a:<24} {str(e['host']):<34} {str(e['port']):<7} {e['service']}")
        say("")
        say("  ^ these host/port/service values are what the BI team needs in order")
        say("    to say which database sync_user belongs to — no credentials here.")

    if not unreadable:
        return info

    head("2c-2. RE-ASKING THE UNREADABLE DSNs VIA oracledb")
    say("  pyodbc lost the ORA- code on these. oracledb decodes it properly.")
    user = (cfg.get("user") or "").strip()
    password = (cfg.get("password") or "").strip()
    try:
        import oracledb
    except ImportError:
        say("  oracledb is not installed — run: pip install oracledb")
        return info

    for dsn in unreadable:
        alias = mapping.get(dsn, dsn)
        e = entries.get(alias.upper())
        say("")
        say(f"  --- {dsn} (alias {alias}) ---")
        if not e or not e.get("host"):
            say("    no host in tnsnames.ora for this alias — cannot retry")
            info["retried"][dsn] = "no tns entry"
            continue
        target = (f"{e['host']}:{e['port'] or 1521}/{e['service']}" if not e["is_sid"]
                  else f"{e['host']}:{e['port'] or 1521}:{e['service']}")
        say(f"    {target}")
        try:
            conn = oracledb.connect(user=user, password=password, dsn=target)
        except Exception as exc:  # noqa: BLE001 — the message IS the result
            msg = str(exc).replace(password, "***") if password else str(exc)
            say(f"    {msg.splitlines()[0][:200]}")
            info["retried"][dsn] = msg.splitlines()[0][:200]
            if "ORA-28000" in msg.upper():
                say("    ACCOUNT LOCKED — stop here and ask IT to unlock sync_user.")
                break
            continue
        say("    LOGIN OK — sync_user IS an account on this database")
        info["retried"][dsn] = "LOGIN OK"
        try:
            cur = conn.cursor()
            cur.execute(ORACLE_EXACT)
            rows = cur.fetchall()
            if not rows:
                cur.execute(ORACLE_FUZZY)
                rows = cur.fetchall()
            if rows:
                say(f"    *** FOUND {len(rows)} matching object(s):")
                for r in rows:
                    say(f"      {r[0]}.{r[1]}  ({r[2]})")
                say("")
                say("    >> Put this in config.yaml:")
                say('         kpi_warehouse:')
                say('           driver: "odbc_direct"')
                say(f'           connection_string: "DRIVER={{Oracle in OraClient19Home1}};DBQ={target};"')
                say(f'           schema: "{rows[0][0]}"')
            else:
                say("    connected, but no NPI/KPI objects are visible to sync_user")
            cur.close()
        finally:
            conn.close()
    return info


# ═══════════════════════════════════════════════════════════════
# 3 — Tableau: data sources and where their data actually lives
# ═══════════════════════════════════════════════════════════════

def load_tns() -> tuple[dict, dict]:
    """(alias -> {host,port,service}, dsn -> alias). Reads files only, no auth."""
    entries: dict = {}
    for f in find_tnsnames():
        try:
            entries.update(parse_tnsnames(f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return entries, dsn_aliases()


def match_host(host: str, entries: dict, mapping: dict) -> list[str]:
    """Which local aliases/DSNs already point at what Tableau calls `host`.

    Tableau's serverAddress for an Oracle connection is usually the TNS ALIAS
    the workbook author typed ("EDWH"), not a resolved hostname. Matching only
    on the hostname made a live run print "no local TNS alias points at this
    host" for EDWH — a database this laptop reaches through two DSNs. So match
    the alias name and the service name as well.
    """
    if not host:
        return []
    h = host.strip().lower()
    aliases = [
        a for a, e in entries.items()
        if h in {
            (e.get("host") or "").strip().lower(),
            a.strip().lower(),
            (e.get("service") or "").strip().lower(),
            (e.get("service") or "").strip().lower().removesuffix(".world"),
        }
    ]
    out = []
    for a in aliases:
        dsns = [d for d, al in mapping.items() if al.upper() == a]
        out.append(f"{a}" + (f" (DSN {', '.join(dsns)})" if dsns else ""))
    return out


# Tableau answers a failed signin with an error CODE and a detail message in the
# body. The shared requests_error() helper folds all of that into "HTTP 401",
# which is why three runs in a row told us nothing new. These codes are distinct
# and actionable, so the raw body is worth one extra request.
TABLEAU_401_CODES = {
    "401000": "SIGNIN_ERROR — the site contentUrl is wrong for this server",
    "401001": "LOGIN_FAILED — bad username/password, or bad PAT NAME/secret pair",
    "401002": "UNAUTHORIZED_ACCESS — credentials accepted but not allowed here",
}


def tableau_signin_probe(cfg: dict, tcfg: dict) -> None:
    """POST auth/signin directly and print what the server actually said."""
    head("3-pre. TABLEAU SIGNIN — the server's own words")
    base = str(tcfg.get("base_url", "")).rstrip("/")
    api_v = str(tcfg.get("api_version", "3.25"))
    site = tcfg.get("content_url", "") or ""
    if not base:
        say("  no tableau.base_url configured")
        return
    try:
        import requests
        import urllib3
    except ImportError:
        say("  requests is not installed")
        return
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    bodies = []
    if tcfg.get("pat_name") and tcfg.get("pat_secret"):
        bodies.append((f"PAT name={tcfg['pat_name']!r}", {"credentials": {
            "personalAccessTokenName": tcfg["pat_name"],
            "personalAccessTokenSecret": tcfg["pat_secret"],
            "site": {"contentUrl": site}}}))
    if cfg.get("user") and cfg.get("password"):
        bodies.append((f"user={cfg['user']!r}", {"credentials": {
            "name": cfg["user"], "password": cfg["password"],
            "site": {"contentUrl": site}}}))

    say(f"  POST {base}/api/{api_v}/auth/signin   site contentUrl={site!r} "
        f"({'Default site' if site == '' else 'named site'})")
    for label, body in bodies:
        try:
            r = requests.post(f"{base}/api/{api_v}/auth/signin",
                              json=body, timeout=30, verify=bool(tcfg.get("verify_ssl", False)),
                              headers={"Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001
            say(f"  {label}: could not reach the server — {type(exc).__name__}")
            continue
        say("")
        say(f"  {label}  ->  HTTP {r.status_code}")
        text = r.text or ""
        for secret in (tcfg.get("pat_secret"), cfg.get("password")):
            if secret:
                text = text.replace(str(secret), "***")
        code = re.search(r'code="(\d+)"', text) or re.search(r'"code"\s*:\s*"(\d+)"', text)
        if code:
            meaning = TABLEAU_401_CODES.get(code.group(1), "")
            say(f"    error code {code.group(1)}" + (f"  = {meaning}" if meaning else ""))
        detail = re.search(r"<detail>(.*?)</detail>", text, re.S)
        if detail:
            say(f"    detail: {detail.group(1).strip()[:300]}")
        else:
            say(f"    body: {text.strip()[:300] or '(empty)'}")

    say("")
    say("  If the code is 401001 with a PAT: the NAME and the SECRET must be from")
    say("  the SAME token. Creating a token in Tableau shows its name once —")
    say("  tableau.pat_name here must be that exact string, not 'Automation' by")
    say("  habit.")


def tableau_luid_lookup(session, base: str, api_v: str, site_id: str,
                        cfg: dict) -> dict:
    """Ask for the three data sources BY LUID, not by name.

    The name listing can come back empty for two very different reasons: the
    data sources were renamed, or this token cannot see them. A direct GET on
    the luid recorded in the May discovery tells those apart — 200 means it is
    there and readable, 404 means gone or invisible, 403 means it exists and
    this token is not allowed near it.
    """
    head("3b. TABLEAU — the three KPI data sources, looked up BY LUID")
    luids = {**DEFAULT_DATASOURCE_LUIDS, **(cfg.get("datasource_luids") or {})}
    out: dict = {}
    for key, luid in luids.items():
        say("")
        say(f"  --- {key}  luid={luid} ---")
        try:
            r = session.get(
                f"{base}/api/{api_v}/sites/{site_id}/datasources/{luid}", timeout=30)
        except Exception as exc:  # noqa: BLE001
            say(f"    request failed: {type(exc).__name__}")
            continue
        say(f"    HTTP {r.status_code}")
        if r.status_code != 200:
            say(f"    {(r.text or '').strip()[:300]}")
            if r.status_code == 404:
                say("    -> renamed, deleted, or not visible to this token")
            elif r.status_code == 403:
                say("    -> it exists; this token is not permitted to read it")
            out[key] = f"HTTP {r.status_code}"
            continue
        ds = (r.json() or {}).get("datasource", {})
        say(f"    name    : {ds.get('name')}")
        say(f"    type    : {ds.get('type')}   project: "
            f"{(ds.get('project') or {}).get('name')}")
        say(f"    updated : {ds.get('updatedAt')}")
        out[key] = ds.get("name")
    return out


def tableau_vds_probe(session, base: str, cfg: dict) -> None:
    """POST read-metadata and print the reply body.

    A live run returned "400 Client Error:" with no message, because the shared
    error helper keeps the status and discards the body — the same blindness
    that cost three rounds on the signin. VDS explains itself in the body: not
    licensed, not enabled, unknown datasource, malformed query. Those are four
    different jobs.
    """
    head("3c. TABLEAU VizQL Data Service — read-metadata, with the reply")
    luids = {**DEFAULT_DATASOURCE_LUIDS, **(cfg.get("datasource_luids") or {})}
    url = f"{base}/api/v1/vizql-data-service/read-metadata"
    say(f"  POST {url}")
    for key, luid in luids.items():
        try:
            r = session.post(url, json={"datasource": {"datasourceLuid": luid}},
                             timeout=60)
        except Exception as exc:  # noqa: BLE001
            say(f"  {key}: request failed — {type(exc).__name__}")
            continue
        say("")
        say(f"  {key}  ->  HTTP {r.status_code}")
        body = (r.text or "").strip()
        say(f"    {body[:500] or '(empty body)'}")
        if r.status_code == 404:
            say("    -> VizQL Data Service is not enabled on this server")
        elif r.status_code == 400:
            say("    -> the server rejected the request itself; the body above "
                "says whether that is the luid, the payload shape, or licensing")


WORKBOOK_REPO_ID = "3651"   # the "ExpressOps KPIs" workbook, from its URL


def tableau_workbook_probe(session, base: str, api_v: str, site_id: str,
                           cfg: dict) -> dict:
    """Ask the ExpressOps KPIs workbook what it is actually connected to.

    The three published data sources recorded in May are now 404 — deleted or
    replaced. The workbook itself is still the thing Tableau renders, so its
    own connections name the live database, schema and account. That is the
    shortest path left to "where do the fact tables live".
    """
    head("3e. TABLEAU — the ExpressOps KPIs workbook's own connections")
    out: dict = {}
    try:
        r = session.get(f"{base}/api/{api_v}/sites/{site_id}/workbooks",
                        params={"pageSize": "1000"}, timeout=60)
        r.raise_for_status()
        books = r.json().get("workbooks", {}).get("workbook", [])
    except Exception as exc:  # noqa: BLE001
        say(f"  could not list workbooks: {type(exc).__name__}")
        return out

    # The numeric id in the URL is the repository id, not the REST luid; it only
    # appears in webpageUrl. Fall back to the name if the URL shape changed.
    wanted_url = f"/workbooks/{WORKBOOK_REPO_ID}"
    hits = [w for w in books if str(w.get("webpageUrl", "")).endswith(wanted_url)]
    if not hits:
        hits = [w for w in books if "expressops" in str(w.get("name", "")).lower()]
    if not hits:
        say(f"  no workbook matching {wanted_url} or name ~ 'ExpressOps' among "
            f"{len(books)} visible workbook(s)")
        return out

    tns_entries, tns_map = load_tns()
    for wb in hits:
        luid = wb.get("id")
        say("")
        say(f"  {wb.get('name')}   (project {(wb.get('project') or {}).get('name')})")
        say(f"    luid    : {luid}")
        say(f"    updated : {wb.get('updatedAt')}")
        try:
            cr = session.get(
                f"{base}/api/{api_v}/sites/{site_id}/workbooks/{luid}/connections",
                timeout=60)
            cr.raise_for_status()
            conns = cr.json().get("connections", {}).get("connection", [])
        except Exception as exc:  # noqa: BLE001
            say(f"    connections NOT READABLE: {type(exc).__name__}")
            continue
        out[str(wb.get("name"))] = conns
        out["_luid"] = luid
        out["_project"] = (wb.get("project") or {}).get("name")
        if not conns:
            say("    no connections reported")
        for c in conns:
            server = str(c.get("serverAddress") or "")
            say(f"    connection: type={c.get('type')} server={server}"
                f":{c.get('serverPort')} db={c.get('databaseName') or '?'} "
                f"as user={c.get('userName') or '?'}")
            hits2 = match_host(server, tns_entries, tns_map)
            if hits2:
                say(f"      -> reachable from this laptop as {'; '.join(hits2)}")
    return out


METADATA_QUERY = """
query WorkbookLineage($luid: String!) {
  workbooks(filter: {luid: $luid}) {
    name
    upstreamDatasources { luid name }
    upstreamTables { name schema fullName }
    upstreamDatabases {
      name
      connectionType
      ... on DatabaseServer { hostName port }
    }
  }
}
"""

METADATA_FALLBACK = """
query WorkbookLineageBasic($luid: String!) {
  workbooks(filter: {luid: $luid}) {
    name
    upstreamDatasources { luid name }
  }
}
"""


def tableau_metadata_probe(session, base: str, workbook_luid: str) -> dict:
    """Ask the Metadata API what the workbook is really built on.

    The workbook's four connections are all type=sqlproxy — Tableau's own proxy
    to PUBLISHED data sources. REST will not name them from the connections
    endpoint, and the luids recorded in May now 404, which means the data
    sources were republished and carry NEW luids. The Metadata API walks the
    lineage properly: workbook -> published data sources -> upstream tables ->
    upstream database, which is both the new luids AND the Oracle host and
    table names behind them.
    """
    head("3f. TABLEAU METADATA API — what the workbook is really built on")
    url = f"{base}/api/metadata/graphql"
    say(f"  POST {url}")
    out: dict = {}
    for label, query in (("full lineage", METADATA_QUERY),
                         ("datasources only", METADATA_FALLBACK)):
        try:
            r = session.post(url, json={"query": query,
                                        "variables": {"luid": workbook_luid}},
                             timeout=90)
        except Exception as exc:  # noqa: BLE001
            say(f"  {label}: request failed — {type(exc).__name__}")
            continue
        if r.status_code != 200:
            say(f"  {label}: HTTP {r.status_code}  {(r.text or '')[:300]}")
            if r.status_code == 404:
                say("    -> the Metadata API is not enabled on this server")
                return out
            continue
        payload = r.json() or {}
        if payload.get("errors"):
            # A schema mismatch kills only the fields it does not know, so try
            # the smaller query before giving up.
            say(f"  {label}: GraphQL errors — "
                f"{json.dumps(payload['errors'])[:300]}")
            continue
        books = (payload.get("data") or {}).get("workbooks") or []
        if not books:
            say(f"  {label}: no workbook returned for that luid")
            continue
        # Collect into a local list. `out = wb` at the bottom of this loop used
        # to clobber the dict the luids were being appended to, so _luids came
        # back empty and 3g silently never ran — with the luids printed on
        # screen the whole time.
        luids_found: list[str] = []
        for wb in books:
            say("")
            say(f"  workbook: {wb.get('name')}")
            for ds in wb.get("upstreamDatasources") or []:
                say(f"    published datasource: {ds.get('name') or '(name not returned)'}")
                say(f"      luid: {ds.get('luid')}")
                if ds.get("luid"):
                    luids_found.append(ds["luid"])
            for db in wb.get("upstreamDatabases") or []:
                say(f"    upstream database: {db.get('name')} "
                    f"({db.get('connectionType')}) "
                    f"host={db.get('hostName')} port={db.get('port')}")
            tables = wb.get("upstreamTables") or []
            if tables:
                say(f"    upstream tables ({len(tables)}):")
                for tb in tables[:40]:
                    say(f"      {tb.get('fullName') or tb.get('name')}"
                        f"   schema={tb.get('schema')}")
            out = dict(wb)
        out["_luids"] = luids_found
        if not luids_found:
            say("")
            say("  no upstream datasource luids came back — 3g cannot run")
        say("")
        say("  Names and upstream tables come back null for this token; 3g reads")
        say("  them over REST instead, which does not need Metadata API rights.")
        return out
    return out


def tableau_probe_luids(session, base: str, api_v: str, site_id: str,
                        luids: list[str]) -> dict:
    """Name each live data source, read its fields, and emit the config block.

    3f gives luids without names (the Metadata API returned name: null for
    these). REST names them, and VDS read-metadata proves each one is actually
    readable and lists its field captions — which is the column map the overlay
    needs. Doing both here means the next run can go straight to section 5.
    """
    head("3g. THE WORKBOOK'S LIVE DATA SOURCES — named, and read")
    resolved: dict = {}
    for luid in luids:
        say("")
        say(f"  --- {luid} ---")
        name = None
        try:
            r = session.get(
                f"{base}/api/{api_v}/sites/{site_id}/datasources/{luid}", timeout=30)
            if r.status_code == 200:
                ds = (r.json() or {}).get("datasource", {})
                name = ds.get("name")
                say(f"    name    : {name}")
                say(f"    type    : {ds.get('type')}   project: "
                    f"{(ds.get('project') or {}).get('name')}")
                say(f"    updated : {ds.get('updatedAt')}")
            else:
                say(f"    REST HTTP {r.status_code} — {(r.text or '')[:160]}")
        except Exception as exc:  # noqa: BLE001
            say(f"    REST failed: {type(exc).__name__}")

        try:
            rm = session.post(
                f"{base}/api/v1/vizql-data-service/read-metadata",
                json={"datasource": {"datasourceLuid": luid}}, timeout=60)
        except Exception as exc:  # noqa: BLE001
            say(f"    VDS failed: {type(exc).__name__}")
            continue
        if rm.status_code != 200:
            say(f"    VDS HTTP {rm.status_code} — {(rm.text or '')[:200]}")
            continue
        payload = rm.json() or {}
        fields = payload.get("data") or payload.get("fields") or []
        captions = []
        for f in fields:
            if isinstance(f, dict):
                cap = f.get("fieldCaption") or f.get("caption") or f.get("name")
                if cap:
                    captions.append(cap)
            elif isinstance(f, str):
                captions.append(f)
        say(f"    VDS OK — {len(captions)} field(s)")
        for cap in captions[:60]:
            say(f"      {cap}")
        if len(captions) > 60:
            say(f"      ... and {len(captions) - 60} more")
        resolved[luid] = {"name": name, "fields": captions}

    # Map what we found back onto wc / wp / combined by name, so the config
    # block can be pasted rather than reasoned about.
    if resolved:
        say("")
        say("  ── READY TO PASTE INTO config/config.yaml ──")
        say("  kpi_warehouse:")
        say('    driver: "tableau_vds"')
        say("    datasource_luids:")
        for key, table in DEFAULT_TABLES.items():
            want = table.lower()
            hit = next((l for l, v in resolved.items()
                        if (v["name"] or "").lower() == want), None)
            if hit is None:
                hit = next((l for l, v in resolved.items()
                            if want.replace("fact_pm_npi_", "") in (v["name"] or "").lower()),
                           None)
            say(f'      {key}: "{hit or "<none matched — pick from the list above>"}"'
                + (f"   # {resolved[hit]['name']}" if hit else ""))
        unmatched = [f"{v['name']} ({l})" for l, v in resolved.items()
                     if not any((v["name"] or "").lower() == tb.lower()
                                for tb in DEFAULT_TABLES.values())]
        if unmatched:
            say("")
            say(f"  not matched to wc/wp/combined: {'; '.join(unmatched)}")
    return resolved


def report_tableau_datasources(cfg: dict, tcfg: dict) -> dict:
    """List the published data sources and ask each for its DB connection."""
    head("3. TABLEAU — published data sources and their underlying connections")
    found: dict = {"datasources": [], "connections": {}, "signin": None}

    # TRY BOTH WAYS IN. The three names the BI team sent —
    # Fact_pm_npi_wc_kpi / _wp_kpi / _wc_wp_combined — are character-for-character
    # the three PUBLISHED TABLEAU DATA SOURCES found in the May discovery, not
    # Oracle tables anyone has ever seen. Put that next to sync_user drawing
    # ORA-01017 from every database this laptop reaches, and the likelier reading
    # is that sync_user is a TABLEAU SERVER login rather than a database one.
    # Testing that costs one HTTP request, so it should not depend on somebody
    # thinking to flip tableau_auth in config.
    attempts: list[tuple[str, dict]] = []
    if str(cfg.get("tableau_auth", "pat")).lower() != "password":
        attempts.append(("the PAT (tableau.pat_name + pat_secret)",
                         {**cfg, "tableau_auth": "pat"}))
    if cfg.get("user") and cfg.get("password"):
        attempts.append((f"sync_user's own name+password ({cfg['user']})",
                         {**cfg, "tableau_auth": "password"}))
    if not attempts:
        attempts.append(("the configured method", cfg))

    driver = None
    for label, attempt_cfg in attempts:
        candidate = TableauVdsDriver(attempt_cfg, tcfg)
        try:
            candidate.session  # noqa: B018 — the property performs the signin
        except Exception as exc:  # noqa: BLE001 — each failure is a data point
            say(f"  signin via {label}: FAILED — {_err(exc)}")
            continue
        say(f"  signin via {label}: OK")
        found["signin"] = label
        driver = candidate
        break

    if driver is None:
        say("")
        say("  Neither way in worked.")
        say("  - 401 on the PAT usually means it needs regenerating: Tableau")
        say("    revokes a token after a stretch of disuse, whatever its expiry.")
        say("  - 401 on name+password means either the password is wrong, or this")
        say("    Tableau site is SSO-only and refuses local logins outright.")
        return found

    session = driver.session
    base = driver.base
    api_v = driver.api_v
    site_id = driver._site_id  # noqa: SLF001 — set during signin, no accessor
    say(f"  site_id={site_id}")
    found["by_luid"] = tableau_luid_lookup(session, base, api_v, site_id, cfg)
    tableau_vds_probe(session, base, cfg)
    wb_info = tableau_workbook_probe(session, base, api_v, site_id, cfg)
    found["workbook"] = wb_info
    if wb_info.get("_luid"):
        meta = tableau_metadata_probe(session, base, wb_info["_luid"])
        found["metadata"] = meta
        live = meta.get("_luids") or []
        if live:
            found["live_datasources"] = tableau_probe_luids(
                session, base, api_v, site_id, live)
    head("3d. TABLEAU — data sources visible by name")

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
    # Widened after a live run: 341 data sources came back and the old filter
    # ("exact name, or contains npi") matched NONE of them, printing a heading
    # with nothing under it — which reads like "there are none" when it really
    # meant "not under a name I guessed". kpi/fact catch the neighbours too.
    def interesting(n: str) -> bool:
        low = n.lower()
        return low in wanted or any(w in low for w in ("npi", "kpi", "fact"))

    matches = [ds for ds in payload if interesting(ds.get("name", ""))]
    # The renamed KPI data sources will still be sitting in the workbook's own
    # project, so show everything there even if the name gives nothing away.
    wb_project = (found.get("workbook") or {}).get("_project")
    if wb_project:
        in_project = [ds for ds in payload
                      if (ds.get("project") or {}).get("name") == wb_project
                      and ds not in matches]
        if in_project:
            say(f"  data sources in the workbook's project "
                f"({wb_project}) — {len(in_project)}:")
            for ds in sorted(in_project, key=lambda d: d.get("name", "")):
                say(f"    - {ds.get('name')}")
                say(f"        luid: {ds.get('id')}   type: {ds.get('type')}   "
                    f"updated: {ds.get('updatedAt')}")
            matches = matches + in_project
    say(f"  {len(payload)} data source(s) visible to this token; "
        f"{len(matches)} look KPI-related:")
    if not matches and payload:
        say("    NONE. This token can see plenty of data sources, just not these.")
        say("    First 40 names, so the real spelling is visible:")
        for ds in sorted(payload, key=lambda d: d.get("name", ""))[:40]:
            say(f"      {ds.get('name')}   [{(ds.get('project') or {}).get('name')}]")
    for ds in matches:
        name = ds.get("name", "")
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
        tns_entries, tns_map = load_tns()
        for c in conns:
            server = str(c.get("serverAddress") or "")
            say(f"        connection: type={c.get('type')} "
                f"server={server}:{c.get('serverPort')} "
                f"as user={c.get('userName')}")
            say("          ^ this is the database the ODBC route needs "
                "(host / port / account)")
            # Turn a bare hostname into something actionable: if this laptop
            # already has a TNS alias or DSN pointing at that host, say so.
            if not server:
                say("          (an extract — no live database behind it)")
                continue
            hits = match_host(server, tns_entries, tns_map)
            if hits:
                say(f"          this host is ALREADY known locally as: "
                    f"{'; '.join(hits)}")
                say("          -> set kpi_warehouse.driver: odbc and dsn to that DSN")
            elif tns_entries:
                say("          no local TNS alias points at this host — IT will "
                    "need to add one, or use driver: odbc_direct")

    driver.close()
    return found


# ═══════════════════════════════════════════════════════════════
# 4 — try every route
# ═══════════════════════════════════════════════════════════════

def try_routes(cfg: dict, tcfg: dict) -> dict[str, object]:
    """Attempt each route against the WC table; return the ones that worked."""
    head("4. ROUTES — which one can actually read Fact_pm_npi_wc_kpi")
    working: dict[str, object] = {}
    pw_cfg = {**cfg, "tableau_auth": "password"}
    candidates = [
        ("tableau_vds (PAT)", lambda: TableauVdsDriver(cfg, tcfg)),
        # Same route, the other credential. Kept separate so the report says
        # WHICH credential reached the data, not just that something did — that
        # distinction is the whole open question about what sync_user is.
        ("tableau_vds (sync_user password)", lambda: TableauVdsDriver(pw_cfg, tcfg)),
        ("odbc", lambda: OdbcDriver(cfg, direct=False)),
        ("odbc_direct", lambda: OdbcDriver(cfg, direct=True)),
    ]
    if not (cfg.get("user") and cfg.get("password")):
        candidates.pop(1)
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
        tns_info = report_tns(dsn_info.get("unreadable", []), cfg)
        dsn_info["tns"] = tns_info
    else:
        head("2b. DSN PROBE — skipped")
        say("  Pass --try-dsns to log in to this machine's own DSNs as sync_user")
        say("  and look for the fact tables there. Left off by default because it")
        say("  attempts a real login against each database.")
    tableau_signin_probe(cfg, tcfg)
    tableau_info = report_tableau_datasources(cfg, tcfg)
    working = try_routes(cfg, tcfg)

    captured: dict = {}
    if working:
        # `working` is filled in the same preference order try_routes attempts,
        # and dicts keep insertion order, so its first key IS the preferred
        # route. Do NOT reach for KpiWarehouseClient.AUTO_ORDER here: the
        # Tableau route is reported as "tableau_vds (PAT)" / "(sync_user
        # password)" so the report can say WHICH credential got in, and those
        # labels never match AUTO_ORDER's bare "tableau_vds". Matching against
        # it silently skipped section 5 for the one route we most expect to
        # work, and StopIteration'd in NEXT STEPS below.
        first = next(iter(working))
        captured = dump_tables(working[first], cfg, save_mock, sample)

    head("NEXT STEPS")
    if not working:
        if cfg.get("user") and cfg.get("password"):
            say("  1. Credentials ARE set and section 1 confirms YAML did not")
            say("     mangle them — that is not the blocker.")
            say("")
            say("     The three names BI sent are the three PUBLISHED TABLEAU DATA")
            say("     SOURCES, not Oracle tables, and sync_user draws ORA-01017 from")
            say("     every database this laptop reaches. Section 3 now tries")
            say("     sync_user as a TABLEAU login as well as the PAT. If that line")
            say("     says OK, sync_user is a Tableau account and the ODBC hunt was")
            say("     always the wrong tree.")
            say("     A Tableau PAT is revoked after a period of disuse, so a 401 in")
            say("     section 3 usually means it needs regenerating in Tableau under")
            say("     My Account Settings -> Personal Access Tokens, then pasting")
            say("     into tableau.pat_secret. That also rotates the one that was")
            say("     committed in plaintext.")
        else:
            say("  1. Fill in kpi_warehouse.user / .password in config/config.yaml.")
        say("  2. If section 3 printed a `connection: ... server=HOST:PORT`, give")
        say("     that host to kpi_warehouse.connection_string (or ask IT for a")
        say("     DSN) and re-run — that is the database sync_user belongs to.")
        if dsn_info.get("tried"):
            say("  3. The local DSNs were tried (section 2b). If every one came back")
            say("     ORA-01017, sync_user is simply not an account on any database")
            say("     this laptop already reaches.")
        else:
            say("  3. Not yet tried: this machine's own DSNs. Run")
            say("       run_kpi_find_dsn.bat   (or --try-dsns)")
            say("     to log in to each Oracle DSN as sync_user and look for the")
            say("     fact tables. DWHSALES / DWHWIS are the obvious candidates.")
        say("  4. Send sections 2b and 2c to the BI team and ask ONE question:")
        say("     which host/port/service is sync_user an account on? Section 2c")
        say("     lists every database this laptop can already reach, so they can")
        say("     answer by pointing at one of them or naming a new one.")
    else:
        used = next(iter(working))
        # The label carries which credential worked; driver: takes the bare name.
        driver_value = used.split(" (")[0]
        say(f"  1. Route that worked: {used}")
        say(f"     Set kpi_warehouse.driver: {driver_value}  (stop auto-probing)")
        if "sync_user password" in used:
            say("     ...and kpi_warehouse.tableau_auth: password")
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
