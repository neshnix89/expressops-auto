"""
Per-MO state persistence for mo_ref_order_monitor.

Each watched MO keeps a JSON state file (history, current stage, per-day
aggregates, cached container key, lifecycle flags) so dwell time and
change-detection survive between 15-min poller runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SAFE = re.compile(r"[^0-9A-Za-z_-]")


def _state_path(state_dir: Path, mo_no: str) -> Path:
    return state_dir / f"state_{_SAFE.sub('_', mo_no)}.json"


def load_state(state_dir: Path, mo_no: str) -> dict | None:
    path = _state_path(state_dir, mo_no)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_dir: Path, state: dict) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(state_dir, state["mo_no"])
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(path)  # atomic-ish: avoid half-written state on crash
    return path


def all_states(state_dir: Path) -> list[dict]:
    """Load every persisted state (used to keep watching MOs already known)."""
    if not state_dir.exists():
        return []
    out = []
    for path in sorted(state_dir.glob("state_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return out
