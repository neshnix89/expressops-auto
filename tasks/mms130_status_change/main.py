"""
mms130_status_change — bulk-set balance-ID status to 1 (Under inspection)
in M3 MMS130 via Playwright, from an Excel/CSV list.

Input columns (header row, any order, case-insensitive):
    Item number | Lot number | Warehouse | Location | [Container]

Modes:
    --mock       Validate the sheet and print the plan. No browser.
    --live       Drive M3. DRY-RUN by default: fills panel A, presses NEXT,
                 snapshots panel B, goes back — nothing is changed.
    --live --apply   Actually sets status 1 and confirms each row.
    --live --discover  Open MMS130, dump panel A fields; with an input file,
                 also go to panel B for the first row and dump it.

Usage:
    python -m tasks.mms130_status_change.main --mock --input list.xlsx
    python -m tasks.mms130_status_change.main --live --input list.xlsx
    python -m tasks.mms130_status_change.main --live --input list.xlsx --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import get_logger  # noqa: E402
from tasks.mms130_status_change.logic import InputRow, read_input, write_results  # noqa: E402

logger = get_logger("mms130_status_change")

NEW_STATUS = "1"  # 1 = Under inspection
OUT_DIR = PROJECT_ROOT / "outputs" / "mms130_status_change"


def _print_plan(rows: list[InputRow]) -> None:
    todo = [r for r in rows if not r.result]
    print(f"\n{len(rows)} row(s) read — {len(todo)} to process, "
          f"{len(rows) - len(todo)} skipped. New status = {NEW_STATUS}\n")
    print(f"{'Row':>4}  {'Item number':<18} {'Lot number':<18} {'Whs':<6} {'Location':<12} {'Container':<12} Note")
    for r in rows:
        print(f"{r.row_no:>4}  {r.item:<18} {r.lot:<18} {r.warehouse:<6} {r.location:<12} {r.container:<12} {r.message}")
    print()


def _connect():
    from clients.m3_h5_client import M3H5Client
    from core.config_loader import load_config
    try:
        config = load_config(mode_override="live")
    except Exception:
        # The H5 client only needs the mode — don't block on unrelated config.
        class _Cfg:
            mode, is_mock = "live", False
        config = _Cfg()
    client = M3H5Client(config)
    client.connect()
    return client


def run_live(rows: list[InputRow], args, debug_dir: Path) -> None:
    from tasks.mms130_status_change.m3_mms130 import (
        STATUS_LABEL_CANDIDATES, MMS130Driver, MMS130Error,
    )

    labels = tuple(args.status_label) if args.status_label else STATUS_LABEL_CANDIDATES
    client = _connect()
    try:
        drv = MMS130Driver(client._page, debug_dir, status_labels=labels)
        drv.open()

        if args.discover:
            print("\n=== MMS130/A fields ===")
            for f in drv.dump_fields():
                print(json.dumps(f))
            first = next((r for r in rows if not r.result), None)
            if first:
                drv.fill_panel_a(first.item, first.lot, first.warehouse, first.location, first.container)
                fields = drv.goto_panel_b("discover")
                print("\n=== MMS130/B fields ===")
                for f in fields:
                    print(json.dumps(f))
                drv.screenshot("discover_panelB")
                drv.back_to_panel_a()
            print(f"\nScreenshots: {debug_dir}")
            return

        todo = [r for r in rows if not r.result]
        for n, r in enumerate(todo, 1):
            tag = f"row{r.row_no}"
            logger.info("[%d/%d] %s", n, len(todo), r.label)
            try:
                drv.fill_panel_a(r.item, r.lot, r.warehouse, r.location, r.container)
                fields = drv.goto_panel_b(tag)
                r.panel_b = {f["label"]: f["value"] for f in fields}
                if args.apply:
                    drv.apply_status(NEW_STATUS, tag)
                    r.result, r.message = "OK", f"status set to {NEW_STATUS}"
                else:
                    drv.back_to_panel_a()
                    r.result, r.message = "DRY-RUN OK", "panel A accepted; not confirmed"
                logger.info("  → %s", r.result)
            except MMS130Error as exc:
                r.result, r.message = "ERROR", str(exc)
                logger.warning("  → ERROR: %s", exc)
                try:
                    drv.back_to_panel_a()
                except Exception as exc2:
                    r.message += f" | recovery failed: {exc2}"
                    logger.error("Cannot recover to panel A — stopping run: %s", exc2)
                    break
    except RuntimeError as exc:
        logger.error("Run stopped: %s", exc)
        for r in rows:
            if not r.result:
                r.result, r.message = "ERROR", f"not processed — run stopped: {exc}"
                break
    finally:
        if args.keep_open:
            input("Browser left open — press Enter to close...")
        client.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MMS130: set balance-ID status to 1 from an Excel list")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="validate input only, no browser")
    mode.add_argument("--live", action="store_true", help="drive M3 H5 (dry-run unless --apply)")
    ap.add_argument("--input", type=Path, help="Excel (.xlsx) or CSV list")
    ap.add_argument("--sheet", help="sheet name (default: first sheet)")
    ap.add_argument("--apply", action="store_true", help="really change the status in M3")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt with --apply")
    ap.add_argument("--discover", action="store_true", help="dump MMS130 panel A/B fields and exit")
    ap.add_argument("--status-label", action="append",
                    help="panel B label of the status field (repeatable) if auto-detect fails")
    ap.add_argument("--start-row", type=int, default=0, help="skip sheet rows before this number")
    ap.add_argument("--limit", type=int, default=0, help="process at most N rows")
    ap.add_argument("--keep-open", action="store_true", help="leave the browser open at the end")
    args = ap.parse_args(argv)

    if args.input is None and not args.discover:
        ap.error("--input is required (except with --discover)")

    rows: list[InputRow] = []
    if args.input:
        if not args.input.exists():
            print(f"[ERROR] input file not found: {args.input}", file=sys.stderr)
            return 1
        try:
            rows = read_input(args.input, args.sheet)
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        if args.start_row:
            rows = [r for r in rows if r.row_no >= args.start_row]
        if args.limit:
            todo_ids = [id(r) for r in rows if not r.result][: args.limit]
            rows = [r for r in rows if r.result or id(r) in todo_ids]
        _print_plan(rows)

    if args.mock:
        for r in rows:
            if not r.result:
                r.result, r.message = "DRY-RUN OK", "mock: input valid, M3 not contacted"
    else:
        if args.apply and not args.discover and not args.yes:
            n = sum(1 for r in rows if not r.result)
            ans = input(f"This will set status {NEW_STATUS} on {n} balance ID(s) in LIVE M3. Type YES to continue: ")
            if ans.strip() != "YES":
                print("Aborted.")
                return 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_live(rows, args, OUT_DIR / f"debug_{stamp}")
        if args.discover:
            return 0

    if rows:
        run_mode = "mock" if args.mock else ("APPLY" if args.apply else "dry-run")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = write_results(rows, OUT_DIR / f"mms130_results_{stamp}.xlsx", run_mode)
        counts = {k: sum(1 for r in rows if r.result == k) for k in ("OK", "DRY-RUN OK", "ERROR", "SKIPPED")}
        pending = sum(1 for r in rows if not r.result)
        print(f"\n[{run_mode}] " + "  ".join(f"{k}: {v}" for k, v in counts.items())
              + (f"  NOT RUN: {pending}" if pending else ""))
        print(f"Results: {out}")
        return 1 if counts["ERROR"] or pending else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
