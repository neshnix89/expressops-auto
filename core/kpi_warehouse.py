"""
core/kpi_warehouse.py — read the NPI KPI fact tables that feed Tableau.

Tableau's "ExpressOps KPIs" workbook (#3651) does not compute the NPI KPIs; it
renders three fact tables that a scheduled job has already computed:

    Fact_pm_npi_wc_kpi           one row per Work Container
    Fact_pm_npi_wp_kpi           one row per Work Package
    Fact_pm_npi_wc_wp_combined   WC joined to its WPs

Reading those tables is how the overlay stops being a second, drifting
implementation of the same arithmetic. This module is the only place that knows
how to reach them.

THREE THINGS ARE DELIBERATELY NOT HARDCODED, because they were not known when
this was written and guessing them would fail silently:

  * the ROUTE — the ``sync_user`` account may be a Tableau Server login (read
    the published data sources through the VizQL Data Service) or a database
    account (read the tables over ODBC). ``driver: auto`` tries both and says
    which one answered.
  * the COLUMN NAMES — every logical field below carries a list of candidate
    column names, matched case- and underscore-insensitively. A name the list
    misses is fixed in config (``kpi_warehouse.columns``), not in code.
  * the CREDENTIALS — they live in config/config.yaml, which is gitignored.

Read-only by construction: the ODBC driver only ever issues SELECT against a
validated identifier, and the Tableau driver only calls signin / read-metadata /
query-datasource / signout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.errors import FriendlyError, missing_dependency, odbc_error, requests_error

# Logical field -> candidate column names, best guess first.
#
# Seeded from the field captions observed on the workbook's own CSV export
# (scripts/tableau_ds_probe.py, discovery 2026-05-29) plus the obvious warehouse
# spellings. Matching ignores case, underscores and spaces, so "WC Issue Key",
# "wc_issue_key" and "WCIssueKey" all resolve to the same logical field.
WC_FIELD_CANDIDATES: dict[str, list[str]] = {
    "issue_key":     ["wc_issue_key", "issue_key", "wc_key", "container_key", "wc_jira_key"],
    "summary":       ["wc_summary", "summary", "wc_title", "title"],
    "status":        ["wc_status", "wc_npi_status", "status", "npi_wc_status"],
    "assignee":      ["wc_assignee", "assignee", "wc_responsible", "responsible", "owner"],
    "location":      ["wc_npi_location", "npi_location", "location", "site", "plant"],
    "order_type":    ["wc_order_type", "order_type"],
    "product_type":  ["wc_product_type", "product_type"],
    "project_id":    ["project_number", "wc_project_id", "project_id", "pt_document",
                      "ptxx_document"],
    "npi_start":     ["wc_npi_start", "npi_start", "wc_start_date", "start_date",
                      "wc_entry_date"],
    # Elapsed for a RUNNING container (clock still ticking) vs the final
    # duration of a closed one. A warehouse usually has both; the overlay only
    # ever shows open containers, so `elapsed` is preferred and `duration` is
    # the fallback.
    "elapsed":       ["wc_running_duration_workdays", "running_duration_workdays",
                      "wc_elapsed_workdays", "elapsed_workdays", "wc_running_workdays"],
    "duration":      ["wc_duration_workdays", "duration_workdays", "wc_total_workdays"],
    "target":        ["wc_target", "target_line", "wc_target_workdays",
                      "target_workdays", "target"],
    "target_hit":    ["wc_target_hit", "target_hit", "wc_kpi_hit", "kpi_hit",
                      "wc_kpi_status"],
    "parked_status": ["wc_parked_status", "parked_status", "parked", "is_parked"],
    "resolution":    ["wc_resolution", "resolution"],
    "resolved":      ["wc_resolution_date", "wc_resolved", "resolution_date",
                      "resolved_date"],
    "as_of":         ["as_of_date", "asof_date", "snapshot_date", "refresh_date",
                      "last_refresh", "etl_date", "load_date", "kpi_date"],
}

WP_FIELD_CANDIDATES: dict[str, list[str]] = {
    "issue_key":     ["wp_issue_key", "issue_key", "wp_key", "wp_jira_key"],
    "container_key": ["wc_issue_key", "container_key", "wp_parent_key", "parent_key",
                      "wc_key"],
    "name":          ["wp_name", "wp_summary", "work_package", "wp_type", "summary",
                      "name"],
    "location":      ["wp_npi_location", "wc_npi_location", "npi_location", "location",
                      "site"],
    "elapsed":       ["wp_running_duration_workdays", "running_duration_workdays",
                      "wp_elapsed_workdays", "elapsed_workdays"],
    "duration":      ["wp_duration_workdays", "duration_workdays"],
    "target":        ["wp_target", "wp_target_workdays", "target_line",
                      "target_workdays", "target"],
    "target_hit":    ["wp_target_hit", "target_hit", "wp_kpi_hit", "kpi_hit",
                      "wp_kpi_status"],
    "status":        ["wp_status", "status"],
    "resolution":    ["wp_resolution", "resolution"],
    "start_date":    ["wp_start_date", "start_date", "wp_entry_date", "wp_created"],
    "end_date":      ["wp_end_date", "end_date", "wp_resolution_date", "wp_resolved"],
}

FIELD_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "wc": WC_FIELD_CANDIDATES,
    "wp": WP_FIELD_CANDIDATES,
    # The combined table carries both sides; resolve it against the union so a
    # single query can drive the whole overlay if that is the shape available.
    "combined": {**WC_FIELD_CANDIDATES,
                 **{f"wp_{k}": v for k, v in WP_FIELD_CANDIDATES.items()}},
}

# Fields the overlay cannot run without, per table.
REQUIRED_FIELDS = {
    "wc": ["issue_key", "elapsed"],
    "wp": ["issue_key", "container_key"],
    "combined": ["issue_key"],
}

DEFAULT_TABLES = {
    "wc": "Fact_pm_npi_wc_kpi",
    "wp": "Fact_pm_npi_wp_kpi",
    "combined": "Fact_pm_npi_wc_wp_combined",
}

DEFAULT_DATASOURCE_LUIDS = {
    "wc": "2c72b33f-dca7-4f80-85b3-41220c5bc355",
    "wp": "456b9a94-7d61-4dc3-98e7-05555c873f85",
    "combined": "eb8a2c04-ca2c-4484-9f7c-1318b61542e7",
}

# A table/schema name goes into SQL by concatenation — there is no bind
# parameter for an identifier. Nothing outside this shape is ever interpolated.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


def _norm(name: str) -> str:
    """Fold a column name for matching: lowercase, drop separators."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def resolve_columns(actual: list[str], table_key: str,
                    overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Map logical field -> real column name for one fact table.

    ``overrides`` (config ``kpi_warehouse.columns.<table_key>``) always wins, so
    a schema the candidate lists do not anticipate is a config edit rather than
    a code change. Unresolved fields are simply absent from the result — callers
    check REQUIRED_FIELDS and degrade for the rest.
    """
    by_norm = {_norm(c): c for c in actual}
    resolved: dict[str, str] = {}

    for logical, candidates in FIELD_CANDIDATES.get(table_key, {}).items():
        for cand in candidates:
            hit = by_norm.get(_norm(cand))
            if hit is not None:
                resolved[logical] = hit
                break

    for logical, column in (overrides or {}).items():
        hit = by_norm.get(_norm(column))
        if hit is None:
            raise FriendlyError(
                f"kpi_warehouse.columns.{table_key}.{logical} points at "
                f"'{column}', which {table_key} does not have",
                f"actual columns: {', '.join(sorted(actual))}",
            )
        resolved[logical] = hit

    return resolved


def check_required(resolved: dict[str, str], actual: list[str], table_key: str) -> None:
    """Raise a FriendlyError naming the real columns if a must-have is missing."""
    missing = [f for f in REQUIRED_FIELDS.get(table_key, []) if f not in resolved]
    if not missing:
        return
    raise FriendlyError(
        f"{table_key} fact table is missing required field(s): {', '.join(missing)}",
        "map them in config.yaml under kpi_warehouse.columns."
        f"{table_key} — actual columns are: {', '.join(sorted(actual))}",
    )


class WarehouseTable:
    """One fact table: its rows, its real columns, and the logical mapping."""

    def __init__(self, table_key: str, rows: list[dict[str, Any]],
                 columns: list[str], overrides: dict[str, str] | None = None,
                 source: str = ""):
        self.table_key = table_key
        self.rows = rows
        self.columns = columns
        self.source = source
        self.mapping = resolve_columns(columns, table_key, overrides)

    def require(self) -> None:
        check_required(self.mapping, self.columns, self.table_key)

    def value(self, row: dict[str, Any], logical: str, default: Any = None) -> Any:
        """Read a logical field off one row, or ``default`` if unmapped/null."""
        col = self.mapping.get(logical)
        if col is None:
            return default
        val = row.get(col)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return default
        return val

    def unmapped(self) -> list[str]:
        """Real columns no logical field claimed — worth eyeballing in discovery."""
        claimed = set(self.mapping.values())
        return [c for c in self.columns if c not in claimed]


# ═══════════════════════════════════════════════════════════════
# DRIVERS
# ═══════════════════════════════════════════════════════════════

class _Driver:
    name = "base"

    def fetch(self, table_key: str, limit: int | None = None) -> tuple[list[dict], list[str]]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class MockDriver(_Driver):
    """Reads mock_data/warehouse/<table_key>.json — {"columns": [...], "rows": [...]}."""

    name = "mock"

    def __init__(self, mock_dir: Path):
        self.mock_dir = Path(mock_dir)

    def fetch(self, table_key: str, limit: int | None = None):
        path = self.mock_dir / "warehouse" / f"{table_key}.json"
        if not path.exists():
            raise FriendlyError(
                f"mock warehouse data not found: {path}",
                "run scripts/kpi_warehouse_discovery.py --save-mock on the "
                "company laptop to capture it",
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("rows", [])
        columns = data.get("columns") or (list(rows[0].keys()) if rows else [])
        if limit is not None:
            rows = rows[:limit]
        return rows, columns


class TableauVdsDriver(_Driver):
    """Published data sources via the VizQL Data Service (read-only)."""

    name = "tableau_vds"

    def __init__(self, cfg: dict, tableau_cfg: dict, logger=None):
        self.cfg = cfg
        self.tcfg = tableau_cfg
        self.logger = logger
        self.base = str(tableau_cfg.get("base_url", "")).rstrip("/")
        self.api_v = str(tableau_cfg.get("api_version", "3.25"))
        self.verify = bool(tableau_cfg.get("verify_ssl", False))
        self._session = None
        self._site_id = None

    # --- auth ---
    def _signin_payload(self) -> dict:
        auth = str(self.cfg.get("tableau_auth", "pat")).lower()
        site = {"contentUrl": self.tcfg.get("content_url", "") or ""}
        if auth == "password":
            user = self.cfg.get("user") or ""
            password = self.cfg.get("password") or ""
            if not user or not password:
                raise FriendlyError(
                    "kpi_warehouse.tableau_auth is 'password' but user/password are blank",
                    "put the sync_user credentials in config.yaml under kpi_warehouse",
                )
            return {"credentials": {"name": user, "password": password, "site": site}}
        name = self.tcfg.get("pat_name") or ""
        secret = self.tcfg.get("pat_secret") or ""
        if not name or not secret:
            raise FriendlyError(
                "no Tableau PAT in config.yaml (tableau.pat_name / tableau.pat_secret)",
                "set them, or set kpi_warehouse.tableau_auth: password to use sync_user",
            )
        return {"credentials": {"personalAccessTokenName": name,
                                "personalAccessTokenSecret": secret, "site": site}}

    @property
    def session(self):
        if self._session is not None:
            return self._session
        try:
            import requests
            import urllib3
        except ImportError as exc:  # pragma: no cover - requests is a hard dep
            raise missing_dependency("requests") from exc
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        s = requests.Session()
        s.verify = self.verify
        s.headers.update({"Accept": "application/json",
                          "Content-Type": "application/json"})
        url = f"{self.base}/api/{self.api_v}/auth/signin"
        try:
            r = s.post(url, data=json.dumps(self._signin_payload()), timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise requests_error(exc, "Tableau", self.base) from exc
        creds = r.json()["credentials"]
        s.headers["X-Tableau-Auth"] = creds["token"]
        self._site_id = creds["site"]["id"]
        self._session = s
        if self.logger:
            self.logger.info("  Tableau signin OK (site %s)", self._site_id)
        return s

    def _luid(self, table_key: str) -> str:
        luids = {**DEFAULT_DATASOURCE_LUIDS, **(self.cfg.get("datasource_luids") or {})}
        luid = luids.get(table_key)
        if not luid:
            raise FriendlyError(
                f"no datasource luid for '{table_key}'",
                "set kpi_warehouse.datasource_luids in config.yaml "
                "(scripts/kpi_warehouse_discovery.py prints them)",
            )
        return luid

    def field_captions(self, table_key: str) -> list[str]:
        """VDS read-metadata -> the data source's field captions."""
        import requests
        url = f"{self.base}/api/v1/vizql-data-service/read-metadata"
        body = {"datasource": {"datasourceLuid": self._luid(table_key)}}
        try:
            r = self.session.post(url, data=json.dumps(body), timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise requests_error(exc, "Tableau VDS", self.base) from exc
        payload = r.json()
        fields = payload.get("data") or payload.get("fields") or []
        captions = []
        for f in fields:
            if isinstance(f, dict):
                cap = f.get("fieldCaption") or f.get("caption") or f.get("name")
                if cap:
                    captions.append(cap)
            elif isinstance(f, str):
                captions.append(f)
        return captions

    def fetch(self, table_key: str, limit: int | None = None):
        import requests
        captions = self.field_captions(table_key)
        if not captions:
            raise FriendlyError(
                f"VDS returned no fields for '{table_key}'",
                "the data source luid may be wrong, or VizQL Data Service is "
                "disabled on this Tableau Server",
            )
        url = f"{self.base}/api/v1/vizql-data-service/query-datasource"
        body = {
            "datasource": {"datasourceLuid": self._luid(table_key)},
            "query": {"fields": [{"fieldCaption": c} for c in captions]},
            # disaggregate: return the underlying rows, not Tableau's default
            # aggregation. Without it every measure comes back SUM()'d over the
            # whole table and the per-container numbers are meaningless.
            "options": {"returnFormat": "OBJECTS", "disaggregate": True, "debug": False},
        }
        try:
            r = self.session.post(url, data=json.dumps(body), timeout=180)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise requests_error(exc, "Tableau VDS", self.base) from exc
        rows = r.json().get("data", [])
        if limit is not None:
            rows = rows[:limit]
        return rows, captions

    def close(self) -> None:
        if self._session is None:
            return
        try:
            self._session.post(f"{self.base}/api/{self.api_v}/auth/signout", timeout=15)
        except Exception:  # noqa: BLE001 — signout failure must not fail a read
            pass
        self._session = None


class OdbcDriver(_Driver):
    """The fact tables straight out of the database, via DSN or DSN-less string."""

    name = "odbc"

    def __init__(self, cfg: dict, logger=None, direct: bool = False):
        self.cfg = cfg
        self.logger = logger
        self.direct = direct
        self._conn = None
        if direct:
            self.name = "odbc_direct"

    def _connection_string(self) -> str:
        user = self.cfg.get("user") or ""
        password = self.cfg.get("password") or ""
        if self.direct:
            base = (self.cfg.get("connection_string") or "").strip()
            if not base:
                raise FriendlyError(
                    "kpi_warehouse.connection_string is blank",
                    "set it (odbc_direct), or use driver: odbc with a DSN name",
                )
        else:
            dsn = (self.cfg.get("dsn") or "").strip()
            if not dsn:
                raise FriendlyError(
                    "kpi_warehouse.dsn is blank",
                    "set the ODBC DSN name, or use driver: odbc_direct with a "
                    "full connection_string",
                )
            base = f"DSN={dsn}"
        if not base.endswith(";"):
            base += ";"
        if user:
            base += f"UID={user};"
        if password:
            base += f"PWD={password};"
        return base

    @property
    def conn(self):
        if self._conn is not None:
            return self._conn
        try:
            import pyodbc
        except ImportError as exc:
            raise missing_dependency("pyodbc") from exc
        cs = self._connection_string()
        try:
            self._conn = pyodbc.connect(cs)
        except Exception as exc:  # noqa: BLE001 — translated below
            raise odbc_error(exc, self.cfg.get("dsn") or "(connection string)") from exc
        if self.logger:
            self.logger.info("  ODBC connect OK (%s)", self.cfg.get("dsn") or "direct")
        return self._conn

    def _qualified(self, table_key: str) -> str:
        tables = {**DEFAULT_TABLES, **(self.cfg.get("tables") or {})}
        table = str(tables.get(table_key) or "").strip()
        schema = str(self.cfg.get("schema") or "").strip()
        for part in ([schema] if schema else []) + [table]:
            if not _IDENT_RE.match(part):
                raise FriendlyError(
                    f"refusing to build SQL from unsafe identifier '{part}'",
                    "kpi_warehouse.schema / tables must be plain identifiers",
                )
        return f"{schema}.{table}" if schema else table

    def fetch(self, table_key: str, limit: int | None = None):
        sql = f"SELECT * FROM {self._qualified(table_key)}"
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = []
            for rec in cur:
                rows.append({col: rec[i] for i, col in enumerate(columns)})
                if limit is not None and len(rows) >= limit:
                    break
        finally:
            cur.close()
        return rows, columns

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


# ═══════════════════════════════════════════════════════════════
# CLIENT
# ═══════════════════════════════════════════════════════════════

class KpiWarehouseClient:
    """Fetch the NPI KPI fact tables, whichever route actually works."""

    #: order tried by ``driver: auto``
    AUTO_ORDER = ("tableau_vds", "odbc", "odbc_direct")

    def __init__(self, config, mock_data_dir: Path | None = None, logger=None,
                 driver_override: str | None = None):
        self.config = config
        self.logger = logger
        self.mock_data_dir = mock_data_dir
        self.cfg = config.get("kpi_warehouse", {}) or {}
        self.tableau_cfg = config.get("tableau", {}) or {}
        self.driver_name = (driver_override or self.cfg.get("driver") or "auto").lower()
        self._driver: _Driver | None = None
        self._cache: dict[str, WarehouseTable] = {}
        self.attempts: list[tuple[str, str]] = []  # (driver, outcome) — diagnostics

    # --- driver selection ---
    def _make(self, name: str) -> _Driver:
        if name == "tableau_vds":
            return TableauVdsDriver(self.cfg, self.tableau_cfg, self.logger)
        if name == "odbc":
            return OdbcDriver(self.cfg, self.logger, direct=False)
        if name == "odbc_direct":
            return OdbcDriver(self.cfg, self.logger, direct=True)
        raise FriendlyError(
            f"unknown kpi_warehouse.driver '{name}'",
            "use one of: auto, tableau_vds, odbc, odbc_direct",
        )

    @property
    def driver(self) -> _Driver:
        if self._driver is not None:
            return self._driver
        if getattr(self.config, "is_mock", False):
            if self.mock_data_dir is None:
                raise FriendlyError(
                    "mock mode needs mock_data_dir",
                    "pass mock_data_dir=... when constructing KpiWarehouseClient",
                )
            self._driver = MockDriver(self.mock_data_dir)
            return self._driver

        if self.driver_name != "auto":
            self._driver = self._make(self.driver_name)
            return self._driver

        # auto: the first route that returns a row wins. Probing 'wc' is the
        # cheapest honest test — a driver that authenticates but cannot read the
        # table is not a working route.
        errors = []
        for name in self.AUTO_ORDER:
            try:
                candidate = self._make(name)
                candidate.fetch("wc", limit=1)
            except Exception as exc:  # noqa: BLE001 — that route is simply out
                detail = getattr(exc, "message", None) or str(exc)
                self.attempts.append((name, f"FAILED: {detail}"))
                errors.append(f"  {name}: {detail}")
                if self.logger:
                    self.logger.debug("  driver %s unavailable: %s", name, detail)
                continue
            self.attempts.append((name, "OK"))
            if self.logger:
                self.logger.info("  KPI warehouse route: %s", name)
            self._driver = candidate
            return self._driver

        raise FriendlyError(
            "no route to the KPI fact tables worked",
            "tried:\n" + "\n".join(errors) +
            "\nrun scripts/kpi_warehouse_discovery.py for a full report",
        )

    # --- reads ---
    def table(self, table_key: str, limit: int | None = None,
              refresh: bool = False) -> WarehouseTable:
        """Fetch one fact table as a :class:`WarehouseTable` (cached per client)."""
        if not refresh and table_key in self._cache:
            return self._cache[table_key]
        rows, columns = self.driver.fetch(table_key, limit=limit)
        overrides = (self.cfg.get("columns") or {}).get(table_key) or {}
        table = WarehouseTable(table_key, rows, columns, overrides,
                               source=self.driver.name)
        if self.logger:
            self.logger.info("  %s: %d row(s), %d column(s) via %s",
                             table_key, len(rows), len(columns), self.driver.name)
        self._cache[table_key] = table
        return table

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
