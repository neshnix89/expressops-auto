"""
JIRA REST API client for Pepperl+Fuchs on-premises JIRA.
Handles PAT auth, SSL bypass, timestamp quirks, and mock mode.
"""

import json
import re
import urllib3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from core.config_loader import Config

# Suppress SSL warnings for on-prem JIRA with self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class JiraClient:
    """JIRA REST API v2 client with mock/live mode support."""

    def __init__(self, config: Config, mock_data_dir: Path | None = None):
        self.config = config
        self.mock_data_dir = mock_data_dir
        self.base_url = config.jira_base_url.rstrip("/")
        self._session = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self.config.jira_pat}",
                "Content-Type": "application/json",
            })
            self._session.verify = self.config.jira_verify_ssl
        return self._session

    def get_issue(self, key: str, expand: str = "") -> dict[str, Any]:
        """Fetch a single JIRA issue by key."""
        if self.config.is_mock:
            return self._load_mock(f"issue_{key}.json")

        params = {}
        if expand:
            params["expand"] = expand
        url = f"{self.base_url}/rest/api/2/issue/{key}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def search(self, jql: str, fields: list[str] | None = None,
               max_results: int = 200, start_at: int = 0) -> dict[str, Any]:
        """
        Execute a JQL search query.

        Returns the raw API response dict with 'issues', 'total', 'startAt', 'maxResults'.
        For paginated results, call repeatedly incrementing start_at.
        """
        if self.config.is_mock:
            # Try to find a mock file named after a sanitized version of the JQL
            safe_name = re.sub(r'[^\w]', '_', jql)[:80]
            return self._load_mock(f"search_{safe_name}.json")

        payload = {
            "jql": jql,
            "maxResults": max_results,
            "startAt": start_at,
        }
        if fields:
            payload["fields"] = fields

        url = f"{self.base_url}/rest/api/2/search"
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def search_all(self, jql: str, fields: list[str] | None = None,
                   page_size: int = 200) -> list[dict[str, Any]]:
        """Paginate through all results of a JQL query."""
        all_issues = []
        start = 0
        while True:
            result = self.search(jql, fields=fields, max_results=page_size, start_at=start)
            issues = result.get("issues", [])
            all_issues.extend(issues)
            if start + len(issues) >= result.get("total", 0):
                break
            start += len(issues)
        return all_issues

    def get_children(self, parent_key: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
        """Fetch child Work Packages of a Work Container using relation() JQL."""
        jql = f'issue in relation("{parent_key}")'
        return self.search_all(jql, fields=fields)

    def add_comment(self, key: str, body: str) -> dict[str, Any]:
        """Add a comment to a JIRA issue. Live mode only."""
        if self.config.is_mock:
            return {"id": "mock-comment", "body": body}

        url = f"{self.base_url}/rest/api/2/issue/{key}/comment"
        resp = self.session.post(url, json={"body": body})
        resp.raise_for_status()
        return resp.json()

    def update_fields(self, key: str, fields: dict[str, Any]) -> bool:
        """Update fields on a JIRA issue. Live mode only."""
        if self.config.is_mock:
            return True

        url = f"{self.base_url}/rest/api/2/issue/{key}"
        resp = self.session.put(url, json={"fields": fields})
        resp.raise_for_status()
        return True

    def transition_issue(self, key: str, transition_id: str) -> bool:
        """Transition an issue to a new status. Live mode only."""
        if self.config.is_mock:
            return True

        url = f"{self.base_url}/rest/api/2/issue/{key}/transitions"
        resp = self.session.post(url, json={"transition": {"id": transition_id}})
        resp.raise_for_status()
        return True

    def get_transitions(self, key: str) -> list[dict[str, Any]]:
        """Get available transitions for an issue."""
        if self.config.is_mock:
            return self._load_mock(f"transitions_{key}.json").get("transitions", [])

        url = f"{self.base_url}/rest/api/2/issue/{key}/transitions"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json().get("transitions", [])

    @staticmethod
    def parse_timestamp(ts: str) -> datetime | None:
        """
        Parse JIRA timestamp, handling on-prem quirks.
        Strips milliseconds and timezone offsets before parsing.
        """
        if not ts:
            return None
        # Strip milliseconds (.123) and timezone offset (+0100 or +01:00)
        cleaned = re.sub(r'\.\d+', '', ts)
        cleaned = re.sub(r'[+-]\d{2}:?\d{2}$', '', cleaned)
        try:
            return datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None

    def _load_mock(self, filename: str) -> dict[str, Any]:
        """Load mock data from the task's mock_data directory."""
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
        """Save API response as mock data for VPS testing."""
        mock_data_dir.mkdir(parents=True, exist_ok=True)
        filepath = mock_data_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return filepath
