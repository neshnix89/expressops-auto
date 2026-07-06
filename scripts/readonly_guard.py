"""
Read-only guard for the discovery probe loop.

Importing this module monkeypatches the transport layers used by the project's
clients so that NO write can reach a live system, no matter what a probe does:

- requests (JIRA + Confluence): only GET / HEAD / OPTIONS are allowed. Any
  POST / PUT / PATCH / DELETE raises PermissionError before it leaves the box.
- pyodbc (M3): cursors may only run SELECT / WITH statements. INSERT / UPDATE /
  DELETE / MERGE / DROP / etc. raise PermissionError.

This is a technical control, not a convention: it makes the fast probe loop safe
to iterate in without per-command scrutiny, because the dangerous, irreversible
actions (writes to production) are simply not reachable. Writes remain a
separate, deliberate, you-reviewed-it step outside this loop.

EDM (Oracle via EDMAdmin.exe) is NOT guarded here — it runs out-of-process.
Keep probes to JIRA / M3; review any EDM probe by hand.

run_probe.py imports this FIRST, before any probe code runs.
"""

from __future__ import annotations

_ALLOWED_HTTP = {"GET", "HEAD", "OPTIONS"}
_ALLOWED_SQL = {"SELECT", "WITH"}


def _is_readonly_post(url: str) -> bool:
    """
    A handful of READ operations use POST verbs. Allow only those; every genuine
    write endpoint stays blocked.

    - JIRA search: POST /rest/api/2/search carries the JQL in the body
      (used by the container audit, which searches + walks relation() children).
    - Tableau session: POST .../auth/signin and .../auth/signout establish and
      tear down a short-lived REST session token. No business content is mutated.
    - Tableau VizQL Data Service: POST .../vizql-data-service/... (a.k.a. /vds/)
      read-metadata and query-datasource are pure reads of published data.

    Every real write — JIRA /comment, /transitions, issue PUT, Confluence page
    writes, Tableau workbook/datasource publish — has a different path and stays
    blocked.
    """
    from urllib.parse import urlsplit

    path = urlsplit(str(url)).path.rstrip("/")
    if path.endswith("/search"):
        return True
    if path.endswith("/auth/signin") or path.endswith("/auth/signout"):
        return True
    if "/vizql-data-service/" in path or "/vds/" in path:
        return True
    return False


def _install_requests_guard() -> None:
    import requests

    _orig_request = requests.Session.request

    def guarded_request(self, method, url, *args, **kwargs):
        m = str(method).upper()
        if m not in _ALLOWED_HTTP and not (m == "POST" and _is_readonly_post(url)):
            raise PermissionError(
                f"[readonly_guard] BLOCKED {m} {url}\n"
                f"  This loop is read-only — writes to JIRA/Confluence are not "
                f"permitted here. Do writes as a separate, reviewed step."
            )
        return _orig_request(self, method, url, *args, **kwargs)

    requests.Session.request = guarded_request


def _sql_keyword(sql: str) -> str:
    s = (sql or "").lstrip()
    if not s:
        return ""
    return s.split(None, 1)[0].upper().rstrip("(")


def _install_pyodbc_guard() -> None:
    try:
        import pyodbc
    except ImportError:
        return  # not installed (e.g. on VPS) — nothing to guard

    _orig_connect = pyodbc.connect

    class _ReadOnlyCursor:
        def __init__(self, cur):
            object.__setattr__(self, "_cur", cur)

        def execute(self, sql, *params):
            kw = _sql_keyword(sql)
            if kw not in _ALLOWED_SQL:
                raise PermissionError(
                    f"[readonly_guard] BLOCKED SQL ({kw or 'empty'}): probes "
                    f"may only SELECT. Writes to M3 are not permitted here."
                )
            self._cur.execute(sql, *params)
            return self

        def executemany(self, *args, **kwargs):
            raise PermissionError(
                "[readonly_guard] BLOCKED executemany(): writes are not "
                "permitted in the discovery loop."
            )

        def __iter__(self):
            return iter(self._cur)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return getattr(self._cur, "__exit__", lambda *a: None)(*exc)

        def __getattr__(self, name):
            return getattr(self._cur, name)

    class _ReadOnlyConnection:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def cursor(self):
            return _ReadOnlyCursor(self._conn.cursor())

        def execute(self, sql, *params):
            # pyodbc connections expose a shortcut .execute()
            kw = _sql_keyword(sql)
            if kw not in _ALLOWED_SQL:
                raise PermissionError(
                    f"[readonly_guard] BLOCKED SQL ({kw or 'empty'}): probes "
                    f"may only SELECT."
                )
            return _ReadOnlyCursor(self._conn.execute(sql, *params))

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def guarded_connect(*args, **kwargs):
        return _ReadOnlyConnection(_orig_connect(*args, **kwargs))

    pyodbc.connect = guarded_connect


_install_requests_guard()
_install_pyodbc_guard()
print("[readonly_guard] ACTIVE - writes to JIRA/Confluence/M3 are blocked.")
