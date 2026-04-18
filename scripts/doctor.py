"""
ExpressOPS doctor — end-to-end environment diagnostic.

Runs a sequence of health checks:

1. Python runtime
2. config.yaml loads without errors
3. JIRA connectivity — one real issue fetched via search
4. M3 ODBC — opens a connection via DSN
5. EDM Oracle — checks EDMAdmin.exe path + dependency
6. Confluence — fetches the MR status page header
7. Per-task inventory (main.py present? last log timestamp?)

Full detail (including tracebacks on unexpected failures) is written to
logs/doctor.log. A concise pass/fail summary is printed to stdout.
Friendly errors raised by the core modules are reused verbatim — no
duplicate phrasing in this file.

Exit code: 0 if every check passed, 1 otherwise.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.errors import FriendlyError  # noqa: E402

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "doctor.log"
TASKS_DIR = PROJECT_ROOT / "tasks"


class DoctorLog:
    """Detail log for doctor runs. Summary goes to stdout separately."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")
        self._fh.write(f"ExpressOPS doctor — {datetime.now().isoformat()}\n")
        self._fh.write("=" * 60 + "\n")

    def section(self, title: str) -> None:
        self._fh.write(f"\n--- {title} ---\n")

    def write(self, text: str) -> None:
        self._fh.write(text.rstrip("\n") + "\n")

    def exc(self) -> None:
        self._fh.write(traceback.format_exc())

    def close(self) -> None:
        self._fh.close()


def _run(log: DoctorLog, section: str, body) -> tuple[bool, str]:
    """Run a check body; translate FriendlyError to a clean summary line."""
    log.section(section)
    try:
        return body()
    except FriendlyError as exc:
        log.write(exc.message)
        if exc.hint:
            log.write(f"hint: {exc.hint}")
        summary = exc.message
        if exc.hint:
            summary = f"{summary} | hint: {exc.hint}"
        return False, summary
    except Exception as exc:
        log.exc()
        return False, f"unexpected {type(exc).__name__}: {exc}"


def check_python(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        log.write(f"sys.version: {sys.version}")
        log.write(f"sys.executable: {sys.executable}")
        v = sys.version_info
        return True, f"Python {v.major}.{v.minor}.{v.micro} OK"
    return _run(log, "Python runtime", body)


def check_config(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        from core.config_loader import CONFIG_PATH, load_config

        log.write(f"CONFIG_PATH: {CONFIG_PATH}")
        config = load_config()
        log.write(f"mode: {config.mode}")
        log.write(f"jira.base_url: {config.jira_base_url}")
        log.write(f"confluence.base_url: {config.confluence_base_url}")
        log.write(f"m3.dsn: {config.m3_dsn}")
        return True, f"config.yaml OK (mode={config.mode})"
    return _run(log, "Config load", body)


def check_jira(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        from core.config_loader import load_config
        from core.jira_client import JiraClient

        config = load_config(mode_override="live")
        jira = JiraClient(config)
        jql = 'issuetype = "Work Container" ORDER BY key DESC'
        log.write(f"JQL: {jql}")
        result = jira.search(jql, fields=["summary", "status"], max_results=1)
        total = result.get("total", 0)
        issues = result.get("issues", []) or []
        log.write(f"total matches: {total}")
        if issues:
            sample = issues[0]
            key = sample.get("key")
            summary = (sample.get("fields") or {}).get("summary", "")
            log.write(f"sample issue: {key} — {summary}")
            return True, f"JIRA OK (matches={total}, sample={key})"
        return True, f"JIRA reachable but no Work Containers matched (total={total})"
    return _run(log, "JIRA connectivity", body)


def check_m3(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        from core.config_loader import load_config
        from core.m3 import M3Client

        config = load_config(mode_override="live")
        m3 = M3Client(config)
        log.write(f"DSN: {config.m3_dsn}")
        conn = m3.connection  # triggers pyodbc import + connect
        log.write(f"connection: {conn}")
        m3.close()
        return True, f"M3 OK (DSN={config.m3_dsn})"
    return _run(log, "M3 ODBC", body)


def check_edm(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        from core.config_loader import load_config
        from core.errors import edm_exe_missing, missing_dependency

        config = load_config(mode_override="live")
        exe = config.edm_python_exe
        log.write(f"edm.python_exe: {exe}")
        if not Path(exe).exists():
            raise edm_exe_missing(exe)
        try:
            import oracledb  # noqa: F401
        except ImportError as exc:
            raise missing_dependency("oracledb") from exc
        return True, f"EDM preflight OK (EDMAdmin at {exe})"
    return _run(log, "EDM Oracle", body)


def check_confluence(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        from core.confluence import ConfluenceClient
        from core.config_loader import load_config

        config = load_config(mode_override="live")
        pages = config.pages or {}
        page_id = pages.get("mr_status_report") or next(iter(pages.values()), None)
        if not page_id:
            return False, "no page IDs configured under pages:"
        confluence = ConfluenceClient(config)
        log.write(f"probe page: {page_id}")
        page = confluence.get_page(page_id, expand="version")
        title = page.get("title", "?")
        return True, f"Confluence OK (page {page_id}: {title})"
    return _run(log, "Confluence", body)


def check_tasks(log: DoctorLog) -> tuple[bool, str]:
    def body() -> tuple[bool, str]:
        if not TASKS_DIR.exists():
            log.write("tasks/ directory not found")
            return False, "tasks/ missing"

        found = 0
        for entry in sorted(TASKS_DIR.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            name = entry.name
            main_py = entry / "main.py"
            has_main = main_py.exists()
            task_log = LOG_DIR / f"{name}.log"
            if task_log.exists():
                ts = datetime.fromtimestamp(task_log.stat().st_mtime)
                last = ts.strftime("%Y-%m-%d %H:%M:%S")
            else:
                last = "never"
            log.write(f"{name}: main.py={'yes' if has_main else 'NO'}, last log={last}")
            found += 1

        if found == 0:
            return False, "no tasks discovered under tasks/"
        return True, f"{found} task(s) discovered"
    return _run(log, "Tasks", body)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = DoctorLog(LOG_FILE)

    print(f"[DOCTOR] Full detail -> {LOG_FILE}")
    print()

    checks = [
        ("Python    ", check_python),
        ("Config    ", check_config),
        ("JIRA      ", check_jira),
        ("M3        ", check_m3),
        ("EDM       ", check_edm),
        ("Confluence", check_confluence),
        ("Tasks     ", check_tasks),
    ]

    results: list[bool] = []
    for label, fn in checks:
        print(f"  [{label}] ...", end=" ", flush=True)
        ok, summary = fn(log)
        mark = "OK  " if ok else "FAIL"
        print(f"{mark}  {summary}")
        log.write(f"RESULT: {mark} — {summary}")
        results.append(ok)

    log.close()

    print()
    if all(results):
        print("[DOCTOR] All checks passed.")
        return 0
    failed = sum(1 for r in results if not r)
    print(f"[DOCTOR] {failed} check(s) failed. See {LOG_FILE} for details.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
