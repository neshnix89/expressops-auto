"""
EDM Oracle database client (Schema: ADMEDP).
NOTE: Live mode requires running Python via EDMAdmin.exe (renamed python.exe)
to bypass SYS.PF_SEC_LOGON_TRIGGER.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.config_loader import Config


class EDMClient:
    """EDM Oracle client with mock/live mode support."""

    def __init__(self, config: Config, mock_data_dir: Path | None = None):
        self.config = config
        self.mock_data_dir = mock_data_dir

    def query(self, sql: str, params: dict | None = None,
              mock_filename: str = "edm_result.json") -> list[dict[str, Any]]:
        """
        Execute a query against EDM Oracle.

        In live mode, this delegates to a subprocess running under EDMAdmin.exe
        because the Oracle logon trigger blocks standard python.exe connections.

        Args:
            sql: Oracle SQL query with :named bind parameters.
            params: Dict of bind parameter values.
            mock_filename: Filename for mock data.
        """
        if self.config.is_mock:
            return self._load_mock(mock_filename)

        # Check if we're already running as EDMAdmin.exe
        current_exe = Path(sys.executable).stem.lower()
        if current_exe == "edmadmin":
            return self._direct_query(sql, params)
        else:
            return self._subprocess_query(sql, params)

    def _direct_query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Direct Oracle query — only works when running as EDMAdmin.exe."""
        import oracledb
        conn = oracledb.connect(self.config.edm_connection_string)
        cursor = conn.cursor()
        cursor.execute(sql, params or {})
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        return [dict(zip(columns, row)) for row in rows]

    def _subprocess_query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """
        Run EDM query via EDMAdmin.exe subprocess.
        Creates a temporary script, executes it under the renamed Python, returns results.
        """
        import tempfile
        query_data = json.dumps({"sql": sql, "params": params or {}, "conn_str": self.config.edm_connection_string})

        script = f'''
import json, sys
try:
    import oracledb
    data = json.loads(sys.argv[1])
    conn = oracledb.connect(data["conn_str"])
    cursor = conn.cursor()
    cursor.execute(data["sql"], data["params"])
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    result = [dict(zip(columns, [str(v) if v is not None else None for v in row])) for row in rows]
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{"error": str(e)}}), file=sys.stderr)
    sys.exit(1)
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(script)
            script_path = f.name

        try:
            result = subprocess.run(
                [self.config.edm_python_exe, script_path, query_data],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise RuntimeError(f"EDM query failed: {result.stderr}")
            return json.loads(result.stdout)
        finally:
            Path(script_path).unlink(missing_ok=True)

    def _load_mock(self, filename: str) -> list[dict[str, Any]]:
        if self.mock_data_dir is None:
            raise ValueError("Mock mode requires mock_data_dir to be set.")
        filepath = self.mock_data_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(
                f"Mock data not found: {filepath}\n"
                f"Run 'ops capture <task>' on company laptop to generate mock data."
            )
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_mock(self, data: Any, filename: str, mock_data_dir: Path) -> Path:
        mock_data_dir.mkdir(parents=True, exist_ok=True)
        filepath = mock_data_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return filepath
