"""
Cross-machine run lock, so two laptops can schedule the SAME task safely.

The goal is active/active: both machines run everything, either one covers when
the other is off, asleep or broken. What must not happen is both executing the
same task at the same moment — two JIRA comments tagging the same people, or two
Confluence republishes racing so the loser's read-modify-write is overwritten.

The lock is a file on the shared drive, created with O_CREAT|O_EXCL, which is
atomic even over SMB: exactly one machine wins the create, the other gets
FileExistsError and skips its run.

Two properties matter more than elegance here:

  * A stale lock must never wedge the schedule. If a laptop is closed mid-run
    the file survives with nobody behind it, so any lock older than `ttl_minutes`
    is broken and taken over. Set the TTL above the task's realistic worst-case
    runtime, not its average.

  * No shared drive must never BLOCK the run. If the folder is unreachable
    (Y: not mapped, VPN down) the task proceeds unlocked and says so. The
    failure mode of a missed report is worse than a rare double-write, and a
    machine that cannot see the share cannot coordinate anyway.

Usage:

    with run_lock("costing_hs_code_trigger", shared_dir, log) as got:
        if not got:
            return 0        # the other machine has it — nothing to do
        ...
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path


def _read(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _age_minutes(info: dict) -> float | None:
    ts = info.get("acquired_at")
    if not ts:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 60
    except ValueError:
        return None


@contextlib.contextmanager
def run_lock(name: str, shared_dir: Path | str | None, log,
             ttl_minutes: float = 30.0, enabled: bool = True):
    """
    Yield True if this machine may run `name`, False if another machine holds it.

    Always yields True when locking is disabled or the shared folder is
    unreachable — see the module docstring: never block the work.
    """
    if not enabled or not shared_dir:
        yield True
        return

    lock_dir = Path(shared_dir) / "locks"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("[lock] shared folder unreachable (%s) — running UNLOCKED. "
                    "If the other laptop runs now too, this task could double up.", exc)
        yield True
        return

    path = lock_dir / f"{name}.lock"
    mine = False
    try:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read(path)
            age = _age_minutes(info)
            holder = info.get("host", "?")
            if age is None or age > ttl_minutes:
                # Stale: the holder died, or the clock/file is unreadable. Taking
                # it over is the lesser evil — the alternative is a task that
                # never runs again until someone notices a file on a share.
                log.warning("[lock] breaking stale lock from %s (age %s min > %s) ",
                            holder, "?" if age is None else round(age, 1), ttl_minutes)
                with contextlib.suppress(OSError):
                    path.unlink()
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except OSError:
                    log.info("[lock] another machine took it first — skipping this run")
                    yield False
                    return
            else:
                log.info("[lock] held by %s since %s — skipping this run "
                         "(the other laptop is covering it)",
                         holder, info.get("acquired_at", "?"))
                yield False
                return
        except OSError as exc:
            log.warning("[lock] could not create lock (%s) — running UNLOCKED", exc)
            yield True
            return

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"host": socket.gethostname(),
                       "user": os.environ.get("USERNAME") or os.environ.get("USER", "?"),
                       "pid": os.getpid(),
                       "acquired_at": datetime.now().isoformat(timespec="seconds")},
                      f)
        mine = True
        log.info("[lock] acquired %s", path.name)
        yield True
    finally:
        if mine:
            with contextlib.suppress(OSError):
                path.unlink()


def lock_status(shared_dir: Path | str | None) -> list[str]:
    """Human-readable list of currently held locks (for diagnostics)."""
    if not shared_dir:
        return []
    lock_dir = Path(shared_dir) / "locks"
    if not lock_dir.exists():
        return []
    out = []
    for p in sorted(lock_dir.glob("*.lock")):
        info = _read(p)
        age = _age_minutes(info)
        out.append(f"{p.stem}: held by {info.get('host', '?')} "
                   f"({'?' if age is None else round(age, 1)} min ago)")
    return out


def acquire(name: str, shared_dir: Path | str | None, log,
            ttl_minutes: float = 30.0, enabled: bool = True) -> bool:
    """
    Non-context form for CLI tasks: acquire, and release at process exit.

    The tasks are short-lived one-shot processes, so `atexit` is a faithful
    "end of run" — and it avoids wrapping an entire main() in a `with`, which
    would be a large, review-hostile re-indentation for no behavioural gain.
    A hard kill leaves the file behind; the TTL takeover covers that.
    """
    cm = run_lock(name, shared_dir, log, ttl_minutes=ttl_minutes, enabled=enabled)
    got = cm.__enter__()
    if got:
        def _release():
            with contextlib.suppress(Exception):
                cm.__exit__(None, None, None)
        atexit.register(_release)
    else:
        cm.__exit__(None, None, None)
    return got
