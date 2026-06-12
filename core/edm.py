"""
EDM Oracle database client (Schema: ADMEDP).

EDM authenticates with Windows-integrated auth (externalauth) in Oracle THICK
mode, and SYS.PF_SEC_LOGON_TRIGGER rejects a normal python.exe — the connecting
process must be a copy of python.exe renamed to EDMAdmin.exe. This client
handles both situations:

  * If the current interpreter IS EDMAdmin.exe → query directly.
  * Otherwise → delegate the query to EDMAdmin.exe (config edm.python_exe) as a
    short-lived subprocess and read the result back as JSON. This lets ordinary
    tasks (running under the normal python.exe) reach EDM without the whole task
    having to run under EDMAdmin.exe.

Connection method (proven; see docs/LEGACY_REFERENCE.md):
    dsn = oracledb.makedsn(host, port, service_name=service)
    oracledb.connect(dsn=dsn, externalauth=True)     # after init_oracle_client()
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.config_loader import Config
from core.errors import (
    FriendlyError,
    edm_exe_missing,
    missing_dependency,
    missing_mock_data,
    oracle_error,
)

# Proven EDM coordinates (overridable via config edm.host / edm.port / edm.service)
DEFAULT_EDM_HOST = "sgp01.sg.pepperl-fuchs.com"
DEFAULT_EDM_PORT = 1521
DEFAULT_EDM_SERVICE = "SGP01EDMEWA.WORLD"
DEFAULT_EDM_EXE = r"C:\Users\tmoghanan\EDMAdmin.exe"


class EDMClient:
    """EDM Oracle client with mock/live mode support (externalauth via EDMAdmin.exe)."""

    def __init__(self, config: Config, mock_data_dir: Path | None = None):
        self.config = config
        self.mock_data_dir = mock_data_dir

    # --- connection coordinates -------------------------------------------
    @property
    def host(self) -> str:
        return self.config.get("edm.host", DEFAULT_EDM_HOST)

    @property
    def port(self) -> int:
        return int(self.config.get("edm.port", DEFAULT_EDM_PORT))

    @property
    def service(self) -> str:
        return self.config.get("edm.service", DEFAULT_EDM_SERVICE)

    @property
    def edm_exe(self) -> str:
        return self.config.get("edm.python_exe", DEFAULT_EDM_EXE)

    # --- public API --------------------------------------------------------
    def query(self, sql: str, params: dict | None = None,
              mock_filename: str = "edm_result.json") -> list[dict[str, Any]]:
        """
        Execute a query against EDM Oracle and return a list of row dicts.

        Args:
            sql: Oracle SQL with :named bind parameters.
            params: Dict of bind parameter values.
            mock_filename: Filename used in mock mode.
        """
        if self.config.is_mock:
            return self._load_mock(mock_filename)

        current_exe = Path(sys.executable).stem.lower()
        if current_exe == "edmadmin":
            return self._direct_query(sql, params)
        return self._subprocess_query(sql, params)

    # --- live paths --------------------------------------------------------
    def _direct_query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Direct Oracle query — only works when running as EDMAdmin.exe."""
        try:
            import oracledb
        except ImportError as exc:
            raise missing_dependency("oracledb") from exc
        try:
            try:
                oracledb.init_oracle_client()  # THICK mode required for externalauth
            except Exception:
                pass  # already initialised, or thick libs already active
            dsn = oracledb.makedsn(self.host, self.port, service_name=self.service)
            conn = oracledb.connect(dsn=dsn, externalauth=True)
            cursor = conn.cursor()
            cursor.execute(sql, params or {})
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            conn.close()
        except oracledb.Error as exc:
            raise oracle_error(exc) from exc
        return [dict(zip(columns, row)) for row in rows]

    def _subprocess_query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Run the query under EDMAdmin.exe and read the JSON result back."""
        import tempfile

        edm_exe = self.edm_exe
        if not Path(edm_exe).exists():
            raise edm_exe_missing(edm_exe)

        query_data = json.dumps({
            "sql": sql,
            "params": params or {},
            "host": self.host,
            "port": self.port,
            "service": self.service,
        })

        script = '''
import json, sys
try:
    import oracledb
    try:
        oracledb.init_oracle_client()   # THICK mode required for externalauth
    except Exception:
        pass
    data = json.loads(sys.argv[1])
    dsn = oracledb.makedsn(data["host"], int(data["port"]), service_name=data["service"])
    conn = oracledb.connect(dsn=dsn, externalauth=True)
    cursor = conn.cursor()
    cursor.execute(data["sql"], data["params"])
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    result = [dict(zip(columns, [str(v) if v is not None else None for v in row])) for row in rows]
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({"error": str(e)}), file=sys.stderr)
    sys.exit(1)
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(script)
            script_path = f.name

        try:
            result = subprocess.run(
                [edm_exe, script_path, query_data],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                detail = (result.stderr.strip() or result.stdout.strip()
                          or f"EDMAdmin exited {result.returncode} with no output "
                             f"(EDMAdmin.exe likely cannot find python3xx.dll — "
                             f"place it inside the Python install dir)")
                raise oracle_error(Exception(detail))
            return json.loads(result.stdout)
        finally:
            Path(script_path).unlink(missing_ok=True)

    # --- mock --------------------------------------------------------------
    def _load_mock(self, filename: str) -> list[dict[str, Any]]:
        if self.mock_data_dir is None:
            raise FriendlyError(
                "mock mode requires mock_data_dir",
                "pass mock_data_dir=... when constructing EDMClient",
            )
        filepath = self.mock_data_dir / filename
        if not filepath.exists():
            raise missing_mock_data(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_mock(self, data: Any, filename: str, mock_data_dir: Path) -> Path:
        mock_data_dir.mkdir(parents=True, exist_ok=True)
        filepath = mock_data_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return filepath
