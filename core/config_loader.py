"""
Configuration loader for ExpressOPS automation.
Reads config.yaml, validates required fields, and provides typed access.
"""

import os
import sys
from pathlib import Path
from typing import Any

import yaml


# Project root = two levels up from this file (core/config_loader.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
CONFIG_EXAMPLE_PATH = PROJECT_ROOT / "config" / "config.example.yaml"


class Config:
    """Typed configuration wrapper."""

    def __init__(self, data: dict[str, Any], mode_override: str | None = None):
        self._data = data
        self.mode = mode_override or data.get("mode", "mock")

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    # --- JIRA ---
    @property
    def jira_base_url(self) -> str:
        return self._data["jira"]["base_url"]

    @property
    def jira_pat(self) -> str:
        return self._data["jira"]["pat"]

    @property
    def jira_verify_ssl(self) -> bool:
        return self._data["jira"].get("verify_ssl", False)

    @property
    def jira_project_key(self) -> str:
        return self._data["jira"].get("project_key", "EXPRESSOPS")

    # --- Confluence ---
    @property
    def confluence_base_url(self) -> str:
        return self._data["confluence"]["base_url"]

    @property
    def confluence_pat(self) -> str:
        return self._data["confluence"]["pat"]

    @property
    def confluence_space_key(self) -> str:
        return self._data["confluence"].get("space_key", "EUDEMHTM0021")

    # --- M3 ---
    @property
    def m3_dsn(self) -> str:
        return self._data["m3"]["dsn"]

    @property
    def m3_schema(self) -> str:
        return self._data["m3"]["schema"]

    # --- EDM ---
    @property
    def edm_python_exe(self) -> str:
        return self._data["edm"]["python_exe"]

    @property
    def edm_schema(self) -> str:
        return self._data["edm"]["schema"]

    @property
    def edm_connection_string(self) -> str:
        return self._data["edm"]["connection_string"]

    # --- Anthropic ---
    @property
    def anthropic_api_key(self) -> str:
        return self._data["anthropic"]["api_key"]

    @property
    def anthropic_model(self) -> str:
        return self._data["anthropic"].get("model", "claude-sonnet-4-20250514")

    # --- Logging ---
    @property
    def log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")

    @property
    def log_dir(self) -> Path:
        return PROJECT_ROOT / self._data.get("logging", {}).get("log_dir", "logs")

    # --- Pages ---
    @property
    def pages(self) -> dict[str, int]:
        return self._data.get("pages", {})

    # --- Raw access for task-specific config ---
    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Access nested config with dot notation: config.get('jira.base_url')"""
        keys = dotted_key.split(".")
        value = self._data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value


def load_config(mode_override: str | None = None) -> Config:
    """
    Load configuration from config.yaml.

    Args:
        mode_override: If set, overrides the mode in config.yaml ('mock' or 'live').

    Returns:
        Config object with typed access to all settings.

    Raises:
        FileNotFoundError: If config.yaml doesn't exist.
        ValueError: If required fields are missing.
    """
    if not CONFIG_PATH.exists():
        print(f"[ERROR] Config file not found: {CONFIG_PATH}")
        print(f"[ERROR] Copy {CONFIG_EXAMPLE_PATH.name} to config.yaml and fill in your values.")
        sys.exit(1)

    with open(CONFIG_PATH, "r") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError("Config file is empty.")

    # Validate required top-level sections exist
    required_sections = ["jira", "confluence", "m3", "edm"]
    missing = [s for s in required_sections if s not in data]
    if missing:
        raise ValueError(f"Missing required config sections: {', '.join(missing)}")

    return Config(data, mode_override)
