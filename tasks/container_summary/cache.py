"""
Incremental-summary cache for container_summary.

Persists ``last_run.json`` — a map ``{container_key: cache_entry}`` used
to skip Opus calls for containers whose JIRA state hasn't changed since
the last run. Writes are atomic (tmp + rename) so a crash mid-write
cannot leave a half-baked file behind.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def load_cache(cache_path: Path, logger: logging.Logger | None = None) -> dict[str, Any]:
    """Load the cache file. Empty dict on missing or corrupted content."""
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        if logger:
            logger.warning(
                "cache %s unreadable (%s) — treating as empty",
                cache_path.name, exc,
            )
        return {}
    if not isinstance(data, dict):
        if logger:
            logger.warning(
                "cache %s is not a JSON object — treating as empty",
                cache_path.name,
            )
        return {}
    return data


def save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    """Atomic write: serialise to .tmp, then rename into place."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, default=str)
    os.replace(tmp_path, cache_path)


def _comment_count(issue: dict[str, Any]) -> int:
    fields = issue.get("fields") or {}
    comments = ((fields.get("comment") or {}).get("comments")) or []
    return len(comments)


def needs_update(cache_entry: dict[str, Any] | None, issue: dict[str, Any]) -> bool:
    """
    True when the container is new or its `updated` timestamp / comment
    count differ from the cached snapshot.
    """
    if not cache_entry:
        return True
    fields = issue.get("fields") or {}
    current_updated = fields.get("updated") or ""
    if current_updated != cache_entry.get("jira_updated"):
        return True
    return _comment_count(issue) != cache_entry.get("last_comment_count", -1)


def update_cache_entry(
    key: str, issue: dict[str, Any], narrative: str,
) -> dict[str, Any]:
    """Build the entry we want to persist for this container after a run."""
    fields = issue.get("fields") or {}
    return {
        "jira_updated": fields.get("updated") or "",
        "last_comment_count": _comment_count(issue),
        "narrative": narrative or "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
