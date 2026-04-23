"""
Confluence REST API client for Pepperl+Fuchs.
Handles reading/writing pages, preserving manual edits, and HTML manipulation.
"""

import json
from pathlib import Path
from typing import Any

import requests
import urllib3

from core.config_loader import Config
from core.errors import FriendlyError, missing_mock_data, requests_error

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ConfluenceClient:
    """Confluence REST API client with mock/live mode support."""

    def __init__(self, config: Config, mock_data_dir: Path | None = None):
        self.config = config
        self.mock_data_dir = mock_data_dir
        self.base_url = config.confluence_base_url.rstrip("/")
        self._session = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self.config.confluence_pat}",
                "Content-Type": "application/json",
            })
            self._session.verify = False
        return self._session

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Run a request and translate transport / HTTP errors into FriendlyError."""
        try:
            resp = self.session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            raise requests_error(exc, "Confluence", self.base_url) from exc

    def get_page(self, page_id: int | str, expand: str = "body.storage,version") -> dict[str, Any]:
        """Fetch a Confluence page by ID."""
        if self.config.is_mock:
            return self._load_mock(f"page_{page_id}.json")

        url = f"{self.base_url}/rest/api/content/{page_id}"
        return self._request("GET", url, params={"expand": expand}).json()

    def get_page_html(self, page_id: int | str) -> str:
        """Get the storage format HTML body of a page."""
        page = self.get_page(page_id)
        return page.get("body", {}).get("storage", {}).get("value", "")

    def update_page(self, page_id: int | str, title: str, html_body: str,
                    version_number: int | None = None) -> dict[str, Any]:
        """
        Update a Confluence page with new HTML content.

        IMPORTANT: Always read the existing page first to get the current version number
        and preserve any manual edits in the content.

        Args:
            page_id: Confluence page ID.
            title: Page title.
            html_body: New HTML content (storage format).
            version_number: If None, auto-increments from current version.
        """
        if self.config.is_mock:
            return {"id": str(page_id), "title": title, "version": {"number": 999}}

        if version_number is None:
            current = self.get_page(page_id)
            version_number = current["version"]["number"] + 1

        payload = {
            "id": str(page_id),
            "type": "page",
            "title": title,
            "body": {
                "storage": {
                    "value": html_body,
                    "representation": "storage"
                }
            },
            "version": {
                "number": version_number
            }
        }

        url = f"{self.base_url}/rest/api/content/{page_id}"
        return self._request("PUT", url, json=payload).json()

    def upload_attachment(
        self,
        page_id: int | str,
        filename: str,
        content: bytes,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        """
        Upload or update a file attachment on a Confluence page.

        Looks up existing attachments on the page; if one with the same
        ``filename`` exists, POSTs a new version to ``{id}/data``.
        Otherwise creates a new attachment. Temporarily drops the
        session ``Content-Type`` header so ``requests`` can generate its
        own multipart boundary.
        """
        if self.config.is_mock:
            return {"id": "mock-attachment"}

        url = f"{self.base_url}/rest/api/content/{page_id}/child/attachment"

        existing = self._request("GET", url).json()
        attach_id = None
        for att in existing.get("results", []):
            if att.get("title") == filename:
                attach_id = att["id"]
                break

        headers = {"X-Atlassian-Token": "nocheck"}
        session_ct = self.session.headers.pop("Content-Type", None)

        try:
            files = {"file": (filename, content, content_type)}
            if attach_id:
                upload_url = f"{url}/{attach_id}/data"
                resp = self._request("POST", upload_url, files=files, headers=headers)
            else:
                resp = self._request("POST", url, files=files, headers=headers)
            return resp.json()
        finally:
            if session_ct:
                self.session.headers["Content-Type"] = session_ct

    def _load_mock(self, filename: str) -> dict[str, Any]:
        if self.mock_data_dir is None:
            raise FriendlyError(
                "mock mode requires mock_data_dir",
                "pass mock_data_dir=... when constructing ConfluenceClient",
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
