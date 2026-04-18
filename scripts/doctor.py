"""
ExpressOPS doctor — end-to-end environment diagnostic.

Runs a sequence of health checks:

1. Python runtime
2. config.yaml loads without errors
3. JIRA connectivity — one real issue fetched via search
4. Per-task inventory (main.py present? last log timestamp?)

Full detail (including tracebacks) is written to logs/doctor.log.
A concise pass/fail summary is printed to stdout so `ops doctor`
shows progress on screen without duplicating verbose output.

Exit code: 0 if every check passed, 1 otherwise.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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


def check_python(log: DoctorLog) -> tuple[bool, str]:
    log.section("Python runtime")
    log.write(f"sys.version: {sys.version}")
    log.write(f"sys.executable: {sys.executable}")
    v = sys.version_info
    return True, f"Python {v.major}.{v.minor}.{v.micro} OK"


def check_config(log: DoctorLog) -> tuple[bool, str]:
    log.section("Config load")
    try:
        from core.config_loader import CONFIG_PATH, load_config

        log.write(f"CONFIG_PATH: {CONFIG_PATH}")
        config = load_config()
        log.write(f"mode: {config.mode}")
        log.write(f"jira.base_url: {config.jira_base_url}")
        log.write(f"confluence.base_url: {config.confluence_base_url}")
        log.write(f"m3.dsn: {config.m3_dsn}")
        return True, f"config.yaml OK (mode={config.mode})"
    except SystemExit:
        log.exc()
        return False, "config.yaml missing or unreadable (load_config exited)"
    except Exception as exc:
        log.exc()
        return False, f"config error: {type(exc).__name__}: {exc}"


def check_jira(log: DoctorLog) -> tuple[bool, str]:
    log.section("JIRA connectivity")
    try:
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
    except Exception as exc:
        log.exc()
        return False, f"JIRA FAIL: {type(exc).__name__}: {exc}"


def check_tasks(log: DoctorLog) -> tuple[bool, str]:
    log.section("Tasks")
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


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = DoctorLog(LOG_FILE)

    print(f"[DOCTOR] Full detail -> {LOG_FILE}")
    print()

    checks = [
        ("Python", check_python),
        ("Config", check_config),
        ("JIRA  ", check_jira),
        ("Tasks ", check_tasks),
    ]

    results: list[bool] = []
    for label, fn in checks:
        print(f"  [{label}] ...", end=" ", flush=True)
        try:
            ok, summary = fn(log)
        except Exception as exc:
            log.exc()
            ok, summary = False, f"{type(exc).__name__}: {exc}"
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
