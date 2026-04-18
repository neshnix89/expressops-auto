"""
Tee runner: execute a command and tee its combined stdout+stderr to both
the console and an append-mode log file, line by line.

Used by `ops fulltest` so the user can watch progress live while the full
transcript is still preserved on disk.

Usage:
    python scripts/tee_run.py <log_file> <command> [args...]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: tee_run.py <log_file> <command> [args...]", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    cmd = sys.argv[2:]

    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "a", encoding="utf-8", buffering=1) as log:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            msg = f"[tee_run] command not found: {cmd[0]}\n"
            sys.stderr.write(msg)
            log.write(msg)
            return 127

        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
        return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
