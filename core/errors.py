"""
Friendly error handling for ExpressOPS core modules.

Core modules raise ``FriendlyError`` carrying a one-line ``message`` and
optional one-line ``hint``. Top-level entry points (task main.py, doctor,
capture) catch it and call :func:`handle_friendly` to print without a
traceback. Non-FriendlyError exceptions still propagate normally so
genuinely unexpected bugs remain visible.
"""

from __future__ import annotations

import sys
from pathlib import Path


class FriendlyError(Exception):
    """Error with a user-facing message + optional fix hint, no traceback needed."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def handle_friendly(exc: FriendlyError) -> int:
    """Print ``[ERROR] message`` and ``[HINT] hint`` to stderr; return exit code 1."""
    print(f"[ERROR] {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"[HINT]  {exc.hint}", file=sys.stderr)
    return 1


# --- config ---------------------------------------------------------------

def config_missing(path: Path) -> FriendlyError:
    return FriendlyError(
        f"config file not found: {path}",
        "copy config/config.example.yaml to config/config.yaml and fill in your values",
    )


def config_invalid(reason: str) -> FriendlyError:
    return FriendlyError(
        f"config.yaml is invalid: {reason}",
        "compare against config/config.example.yaml",
    )


def yaml_error(exc: Exception, path: Path) -> FriendlyError:
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        return FriendlyError(
            f"{path.name} has a YAML syntax error at line {mark.line + 1}, column {mark.column + 1}",
            "open the file and fix the YAML at that location",
        )
    return FriendlyError(f"{path.name} has a YAML syntax error: {exc}")


# --- dependencies / mocks -------------------------------------------------

def missing_dependency(module_name: str, install_cmd: str | None = None) -> FriendlyError:
    cmd = install_cmd or f"pip install {module_name}"
    return FriendlyError(
        f"Python dependency '{module_name}' is not installed",
        f"run: {cmd}",
    )


def missing_mock_data(filepath: Path) -> FriendlyError:
    return FriendlyError(
        f"mock data not found: {filepath.name}",
        "run 'ops capture <task>' on the company laptop to generate it",
    )


# --- requests (JIRA, Confluence) ------------------------------------------

def requests_error(exc: Exception, service: str, base_url: str) -> FriendlyError:
    """Translate a ``requests``/urllib3 exception into a FriendlyError."""
    import requests

    if isinstance(exc, requests.exceptions.ConnectionError):
        return FriendlyError(
            f"cannot reach {service} at {base_url}",
            "check VPN/network; the host is only reachable from the company LAN",
        )
    if isinstance(exc, requests.exceptions.Timeout):
        return FriendlyError(
            f"{service} request timed out",
            "retry, or check whether the service is slow/overloaded",
        )
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        status = resp.status_code if resp is not None else "?"
        pat_key = f"{service.lower()}.pat"
        if status in (401, 403):
            return FriendlyError(
                f"{service} auth failed (HTTP {status})",
                f"check {pat_key} in config/config.yaml (PAT may be expired)",
            )
        if status == 404:
            return FriendlyError(
                f"{service} returned HTTP 404",
                "verify the issue key / page ID / endpoint in your request",
            )
        if status == 429:
            return FriendlyError(
                f"{service} rate-limited (HTTP 429)",
                "back off and retry",
            )
        return FriendlyError(f"{service} HTTP {status}: {exc}")
    return FriendlyError(f"{service} request failed: {type(exc).__name__}: {exc}")


# --- ODBC (M3) ------------------------------------------------------------

def odbc_error(exc: Exception, dsn: str) -> FriendlyError:
    msg = str(exc)
    upper = msg.upper()
    if "IM002" in upper or "DATA SOURCE NAME NOT FOUND" in upper:
        return FriendlyError(
            f"ODBC DSN '{dsn}' not found on this machine",
            "open Windows 'ODBC Data Sources (64-bit)' and confirm the DSN exists",
        )
    if "08001" in upper or "SERVER IS NOT" in upper or "COULD NOT OPEN" in upper:
        return FriendlyError(
            f"cannot reach M3 via DSN '{dsn}'",
            "check VPN/network; M3 ODBC uses Windows integrated auth",
        )
    if "28000" in upper or "LOGIN FAILED" in upper:
        return FriendlyError(
            f"M3 login failed via DSN '{dsn}'",
            "confirm your Windows account has M3 read access",
        )
    return FriendlyError(f"M3 ODBC error: {type(exc).__name__}: {exc}")


# --- Oracle (EDM) ---------------------------------------------------------

def oracle_error(exc: Exception) -> FriendlyError:
    msg = str(exc)
    upper = msg.upper()
    if "ORA-01017" in upper or "INVALID USERNAME/PASSWORD" in upper:
        return FriendlyError(
            "EDM auth failed (ORA-01017)",
            "check edm.connection_string in config/config.yaml",
        )
    if "ORA-12170" in upper or "TNS" in upper or "COULD NOT RESOLVE" in upper:
        return FriendlyError(
            "cannot reach EDM Oracle",
            "check VPN/network and the host/service in edm.connection_string",
        )
    if "PF_SEC_LOGON_TRIGGER" in upper or "ORA-20" in upper:
        return FriendlyError(
            "EDM logon trigger rejected this connection",
            "run Python via EDMAdmin.exe (renamed copy of python.exe); see CLAUDE.md",
        )
    return FriendlyError(f"EDM error: {type(exc).__name__}: {exc}")


def edm_exe_missing(path: str) -> FriendlyError:
    return FriendlyError(
        f"EDMAdmin.exe not found at: {path}",
        "copy python.exe to EDMAdmin.exe on the company laptop and set edm.python_exe in config",
    )
