"""
Access check for a machine that will RUN the automations.

Answers one question: what can this Windows account actually reach? Every check
is read-only apart from one temp file written to the shared folder — and that
write is itself a requirement, because the shared state lives there.

The point is a SINGLE IT request. Piecemeal discovery ("DSN missing" one day,
"no read on PFODS" the next, "can't write to the share" the day after) turns a
one-ticket setup into three. Failures are collected and printed as a ready-to-
paste request naming the DSN, schema, tables and paths involved.

Secrets are NEVER printed — tokens appear as "SET (n chars)".

    python scripts\\check_access.py            (or double-click check_access.bat)
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEP = "=" * 72

# Tables the repo reads, with the task that needs each. Checked individually:
# a login can succeed while a single table is denied, and "M3 works" is then
# wrong in a way that only shows up at runtime.
M3_TABLES = [
    ("MWOHED_AP", "mo_ref_order_monitor — MO header, ref order no + status"),
    ("MITMAS_AP", "item master (PLC status, description)"),
    ("MITBAL", "item/warehouse balance"),
    ("MPDHED", "product structure header"),
    ("MPDMAT", "BOM materials"),
    ("MPDOPE", "routing operations"),
]

results: list[tuple[str, bool, str]] = []
it_asks: list[str] = []


def check(label: str, ok: bool, detail: str = "", it_ask: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok and it_ask:
        it_asks.append(it_ask)
    return ok


def head(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")


def redact(v) -> str:
    s = str(v or "")
    return f"SET ({len(s)} chars)" if s.strip() else "EMPTY"


def main() -> int:  # noqa: C901 — a flat list of checks reads better than nesting
    print(f"expressops access check — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"machine : {socket.gethostname()}")
    print(f"user    : {os.environ.get('USERNAME') or os.environ.get('USER') or '?'}")
    print(f"python  : {sys.version.split()[0]}  ({sys.executable})")

    # ── 1. Python packages ───────────────────────────────────────────
    head("1. PYTHON PACKAGES")
    import importlib.util
    for mod, pkg in (("yaml", "PyYAML"), ("requests", "requests"), ("pyodbc", "pyodbc")):
        check(f"import {mod}", importlib.util.find_spec(mod) is not None, pkg,
              it_ask=f"install the Python package {pkg} (pip may need proxy settings)")

    # ── 2. Config ────────────────────────────────────────────────────
    head("2. CONFIG")
    cfg = None
    try:
        from core.config_loader import load_config
        cfg = load_config()
        check("config/config.yaml loads", True)
        print(f"       jira.base_url : {cfg.jira_base_url}")
        print(f"       jira.pat      : {redact(cfg.jira_pat)}")
        print(f"       m3.dsn        : {cfg.m3_dsn}   schema: {cfg.m3_schema}")
    except Exception as exc:  # noqa: BLE001
        check("config/config.yaml loads", False, str(exc)[:150])
        print("\n  Cannot continue without config. Run the installer first.")
        return 1

    # ── 3. JIRA ──────────────────────────────────────────────────────
    head("3. JIRA (read-only)")
    who = None
    try:
        import requests
        import urllib3
        urllib3.disable_warnings()
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {cfg.jira_pat}",
                          "Content-Type": "application/json"})
        s.verify = cfg.jira_verify_ssl
        r = s.get(f"{cfg.jira_base_url}/rest/api/2/myself", timeout=30)
        if r.status_code == 200:
            who = r.json()
            # WHOSE token this is matters: a config.yaml copied from another
            # machine authenticates fine and silently signs every write with
            # the wrong name.
            check("token authenticates", True,
                  f"{who.get('displayName')} ({who.get('name')})")
            # A config.yaml copied from another machine authenticates perfectly
            # and signs every JIRA write with the wrong name — including the
            # tracking table footer. Cheap to warn, expensive to discover late.
            winuser = (os.environ.get("USERNAME") or "").lower()
            jirauser = str(who.get("name") or "").lower()
            if winuser and jirauser and winuser not in jirauser and jirauser not in winuser:
                print(f"       WARNING: logged in as '{winuser}' but this token "
                      f"belongs to '{jirauser}'.")
                print("       If that is not deliberate, the config.yaml was copied "
                      "from another machine — replace it with your own PAT.")
        else:
            check("token authenticates", False, f"HTTP {r.status_code}",
                  it_ask=None if r.status_code == 401 else
                  f"JIRA returned HTTP {r.status_code} for this account")
            if r.status_code == 401:
                print("       -> the PAT is wrong or expired. Self-service: JIRA "
                      "-> Profile -> Personal Access Tokens. Not an IT request.")
    except Exception as exc:  # noqa: BLE001
        check("token authenticates", False, f"{type(exc).__name__}: {exc}"[:150],
              it_ask=f"allow HTTPS access to {cfg.jira_base_url} from this machine")

    if who:
        try:
            jql = cfg.get("mo_ref_order_monitor.jql", "")
            r = s.get(f"{cfg.jira_base_url}/rest/api/2/search",
                      params={"jql": jql, "maxResults": 1}, timeout=60)
            ok = r.status_code == 200
            n = r.json().get("total", "?") if ok else r.text[:120]
            check("container JQL runs", ok, f"{n} issues visible" if ok else str(n),
                  it_ask="grant browse permission on the NPI Work Container "
                         "projects (the saved filter 25423 universe)")
        except Exception as exc:  # noqa: BLE001
            check("container JQL runs", False, str(exc)[:150])

    # ── 4. M3 / ODS ──────────────────────────────────────────────────
    head("4. M3 via ODBC")
    conn = None
    try:
        import pyodbc
        try:
            conn = pyodbc.connect(f"DSN={cfg.m3_dsn}")
            check(f"connect DSN={cfg.m3_dsn}", True)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            hint = ""
            if "28000" in msg or "ORA-01017" in msg.upper():
                hint = "invalid username/password"
            elif "IM002" in msg:
                hint = "DSN not defined on this machine"
            check(f"connect DSN={cfg.m3_dsn}", False, (hint or msg[:150]),
                  it_ask=f"grant this Windows account read access to M3/ODS via "
                         f"ODBC DSN '{cfg.m3_dsn}' (Oracle). It authenticates but "
                         f"the login is rejected ({hint or 'see error'}) — the same "
                         f"DSN works on the primary NPI laptop, so this is an "
                         f"account/credential difference, not a missing driver.")
    except ImportError:
        check("pyodbc available", False, "not installed")

    if conn:
        cur = conn.cursor()
        for table, why in M3_TABLES:
            try:
                # WHERE 1=0: asks the server for permission and column metadata
                # without transferring a single row.
                cur.execute(f"SELECT * FROM {cfg.m3_schema}.{table} WHERE 1=0")
                check(f"{cfg.m3_schema}.{table}", True, why)
            except Exception as exc:  # noqa: BLE001
                check(f"{cfg.m3_schema}.{table}", False, str(exc)[:110],
                      it_ask=f"grant SELECT on {cfg.m3_schema}.{table} ({why})")
        conn.close()

    # ── 5. Shared folder ─────────────────────────────────────────────
    head("5. SHARED STATE FOLDER (read AND write)")
    state_dir = cfg.get("mo_ref_order_monitor.state_dir", "")
    p = Path(state_dir) if state_dir else None
    if not p or not p.is_absolute():
        check("state_dir is a shared path", False,
              f"{state_dir!r} is local — the two laptops would not share history")
    else:
        if check(f"reach {p}", p.exists(), it_ask=f"map the network drive holding {p}"):
            files = sorted(p.glob("state_*.json"))
            print(f"       {len(files)} existing state file(s) — "
                  f"{'shared history visible' if files else 'none yet'}")
            # Write access is not optional: this machine must persist state and
            # queue alerts here, and a read-only share fails silently at 09:15.
            probe = p / f".access_check_{os.getpid()}.tmp"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                check("write to shared folder", True)
            except Exception as exc:  # noqa: BLE001
                check("write to shared folder", False, str(exc)[:120],
                      it_ask=f"grant WRITE access to {p} (and its parent) — the "
                             f"automation stores per-MO state and the alert queue there")

    # ── 6. Webex desktop ─────────────────────────────────────────────
    head("6. WEBEX (desktop transport)")
    link = cfg.get("mo_ref_order_monitor.webex.space_link", "")
    check("space_link configured", bool(str(link).strip()), redact(link))
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "@(Get-Process | Where-Object { $_.ProcessName -match "
                 "'^(Webex|CiscoCollabHost|webexmta)$' }).Count"],
                capture_output=True, timeout=60, check=False)
            n = (out.stdout or b"").decode("utf-8", "replace").strip()
            check("Webex desktop running", n.isdigit() and int(n) > 0,
                  f"{n} process(es)")
        except Exception as exc:  # noqa: BLE001
            check("Webex desktop running", False, str(exc)[:100])
    else:
        print("  (skipped — not Windows)")

    # ── Summary ──────────────────────────────────────────────────────
    head("SUMMARY")
    failed = [r for r in results if not r[1]]
    print(f"  {len(results) - len(failed)} passed, {len(failed)} failed")

    if not failed:
        print("\n  Everything this machine needs is in place. No IT request required.")
        return 0

    print("\n  FAILED:")
    for label, _, detail in failed:
        print(f"    - {label}" + (f" ({detail})" if detail else ""))

    if it_asks:
        head("DRAFT IT REQUEST (paste into one ticket)")
        print(f"Machine: {socket.gethostname()}")
        print(f"Windows account: {os.environ.get('USERNAME')}")
        print("Purpose: run the ExpressOPS NPI automation (reads M3/ODS and JIRA,")
        print("         writes status tables back to JIRA Work Containers).")
        print("\nAccess needed:")
        for i, ask in enumerate(it_asks, 1):
            print(f"  {i}. {ask}")
        print("\nAlready working on this machine: " +
              ", ".join(label for label, ok, _ in results if ok) or "(nothing)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
