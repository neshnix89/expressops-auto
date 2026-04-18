"""
Show status overview of all tasks.
Reads log files to determine last run time and result.
Called by: ops status
"""

import os
import re
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"
LOGS_DIR = PROJECT_ROOT / "logs"


def get_task_status(task_name: str) -> dict:
    """Check log file for a task's last run status."""
    log_file = LOGS_DIR / f"{task_name}.log"

    if not log_file.exists():
        return {"task": task_name, "last_run": "Never", "status": "NOT RUN", "detail": ""}

    stat = log_file.stat()
    last_modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    # Read last few lines to determine success/failure
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    last_lines = "".join(lines[-5:]) if lines else ""

    if "ERROR" in last_lines:
        status = "FAILED"
        # Extract last error message
        error_lines = [l.strip() for l in lines if "ERROR" in l]
        detail = error_lines[-1][-80:] if error_lines else ""
    elif "completed" in last_lines.lower() or "done" in last_lines.lower() or "published" in last_lines.lower():
        status = "OK"
        detail = ""
    else:
        status = "UNKNOWN"
        detail = lines[-1].strip()[-80:] if lines else ""

    return {
        "task": task_name,
        "last_run": last_modified,
        "status": status,
        "detail": detail,
    }


def main():
    # Find all tasks (directories under tasks/ that contain main.py)
    tasks = []
    if TASKS_DIR.exists():
        for task_dir in sorted(TASKS_DIR.iterdir()):
            if task_dir.is_dir() and (task_dir / "main.py").exists():
                tasks.append(task_dir.name)

    if not tasks:
        print("No tasks found. Create your first task in tasks/<task_name>/")
        return

    # Print status table
    print(f"\n{'Task':<30} {'Last Run':<22} {'Status':<10} {'Detail'}")
    print("=" * 100)

    for task_name in tasks:
        info = get_task_status(task_name)
        status_marker = {
            "OK": "[OK]    ",
            "FAILED": "[FAIL]  ",
            "NOT RUN": "[--]    ",
            "UNKNOWN": "[??]    ",
        }.get(info["status"], "[??]    ")

        print(f"{info['task']:<30} {info['last_run']:<22} {status_marker:<10} {info['detail']}")

    print()


if __name__ == "__main__":
    main()
