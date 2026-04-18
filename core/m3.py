"""
M3 ERP client via ODBC (DSN: ODSSG, Schema: PFODS).
Tables use _AP suffix. No REST API available — ODBC is the path.
"""

import csv
import json
from pathlib import Path
from typing import Any

from core.config_loader import Config


class M3Client:
    """M3 ERP database client with mock/live mode support."""

    def __init__(self, config: Config, mock_data_dir: Path | None = None):
        self.config = config
        self.mock_data_dir = mock_data_dir
        self._conn = None

    @property
    def connection(self):
        """Lazy ODBC connection — only created when first needed."""
        if self._conn is None:
            import pyodbc
            self._conn = pyodbc.connect(f"DSN={self.config.m3_dsn}")
        return self._conn

    def query(self, sql: str, params: tuple = (), mock_filename: str = "query_result.json") -> list[dict[str, Any]]:
        """
        Execute a SQL query against M3.

        Args:
            sql: SQL query with positional ? placeholders.
            params: Tuple of parameter values.
            mock_filename: Filename to use for mock data (read in mock mode, save target for capture).

        Returns:
            List of dicts, one per row.
        """
        if self.config.is_mock:
            return self._load_mock(mock_filename)

        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def get_table_columns(self, table_name: str) -> list[str]:
        """Get column names for a table. Useful during data discovery."""
        if self.config.is_mock:
            return self._load_mock(f"columns_{table_name}.json")

        cursor = self.connection.cursor()
        # Query with LIMIT 0 to get structure without data
        cursor.execute(f"SELECT * FROM {self.config.m3_schema}.{table_name} WHERE 1=0")
        return [desc[0] for desc in cursor.description]

    def explore_table(self, table_name: str, limit: int = 10, where: str = "") -> list[dict[str, Any]]:
        """
        Fetch sample rows from a table. For data discovery only.

        Args:
            table_name: Table name without schema prefix (e.g., 'MPDHED_AP').
            limit: Max rows to return.
            where: Optional WHERE clause (without the WHERE keyword).
        """
        sql = f"SELECT * FROM {self.config.m3_schema}.{table_name}"
        if where:
            sql += f" WHERE {where}"
        sql += f" FETCH FIRST {limit} ROWS ONLY"

        if self.config.is_mock:
            return self._load_mock(f"explore_{table_name}.json")

        cursor = self.connection.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def close(self):
        """Close the ODBC connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _load_mock(self, filename: str) -> list[dict[str, Any]] | list[str]:
        """Load mock data from task's mock_data directory."""
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
        """Save query result as mock data for VPS testing."""
        mock_data_dir.mkdir(parents=True, exist_ok=True)
        filepath = mock_data_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return filepath
