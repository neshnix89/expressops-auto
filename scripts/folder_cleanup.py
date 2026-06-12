"""
FOLDER CLEANUP — moves approved EXTRA files out of the laptop expressops-auto
folder into a dated archive OUTSIDE the repo. Moves, never deletes — fully
reversible (writes a manifest + a restore_<stamp>.bat).

Default is a PREVIEW (dry run): it prints exactly what WOULD move and moves
nothing. Pass --apply to actually move.

Selection is rule-based and reuses folder_audit's protected/tracked manifest as
the single source of truth, so anything PROTECTED or part of the repo can never
be moved, no matter what the rules say.

Approved for archiving (confirmed with the user 2026-06-12):
  - root  debug_* / phase_b_* / discover_* / result_*   (dev + debug scratch)
  - outputs/**                                           (regenerated)
  - scripts/_perrow_scan_results.json                    (leftover scan result)
  - logs/container_template_audit.batch.log, logs/setup_edmadmin.txt  (stale)
  - config/config.yaml.bak                               (stale secret backup)
  - mock_data/*.xml   (ROOT-level only; Phase-B leftovers)

Explicitly KEPT (never moved):
  - logs/mr_status_report.log   (active daily log)
  - tasks/**/mock_data/**       (real API captures for --mock testing)
  - .ops_config                 (live ops state)
  - everything PROTECTED or TRACKED
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.folder_audit import (  # reuse the single source of truth
    INSTALL_DIR,
    ROLLUP_DIRS,
    TRACKED,
    hsize,
    is_protected,
    norm,
)

ARCHIVE_ROOT = Path(r"C:\Users\tmoghanan\Documents\AI\expressops-auto-archive")

ROOT_MOVE_PREFIXES = ("debug_", "phase_b_", "discover_", "result_")

# Hard never-move set (belt-and-suspenders on top of protected/tracked checks).
KEEP_EXACT = {
    "logs/mr_status_report.log",
    ".ops_config",
}


def _in_rollup(rel: Path) -> bool:
    parts_lower = {seg.lower() for seg in rel.parts}
    return any(r in parts_lower for r in ROLLUP_DIRS)


def should_move(rel: Path) -> bool:
    """Decide if an EXTRA file is approved for archiving."""
    rn = norm(rel)
    parts = rel.as_posix().split("/")
    top = parts[0] if len(parts) > 1 else "(root)"
    base = parts[-1]

    # --- hard guards: never move these, whatever the rules say ---
    if rn in KEEP_EXACT or base == ".ops_config":
        return False
    if is_protected(rn) or rn in TRACKED:
        return False
    if _in_rollup(rel):
        return False
    # keep task mock_data captures
    if top == "tasks" and "mock_data" in parts:
        return False

    # --- approved include rules ---
    if top == "(root)" and base.startswith(ROOT_MOVE_PREFIXES):
        return True
    if rn.startswith("outputs/"):
        return True
    if rn == "scripts/_perrow_scan_results.json":
        return True
    if rn in ("logs/container_template_audit.batch.log", "logs/setup_edmadmin.txt"):
        return True
    if rn == "config/config.yaml.bak":
        return True
    if top == "mock_data" and base.lower().endswith(".xml"):  # ROOT mock_data only
        return True
    return False


def collect() -> list[Path]:
    out: list[Path] = []
    for p in INSTALL_DIR.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(INSTALL_DIR)
        if should_move(rel):
            out.append(rel)
    return sorted(out, key=lambda r: r.as_posix().lower())


def main() -> int:
    apply = "--apply" in sys.argv
    if not INSTALL_DIR.is_dir():
        print(f"ERROR: {INSTALL_DIR} not found — are you on the company laptop?")
        return 1

    rels = collect()
    total = 0
    for rel in rels:
        try:
            total += (INSTALL_DIR / rel).stat().st_size
        except OSError:
            pass

    mode = "APPLY (moving files)" if apply else "PREVIEW (dry run — nothing moved)"
    print(f"=== FOLDER CLEANUP — {mode} ===")
    print(f"Source : {INSTALL_DIR}")
    stamp = datetime.now().strftime("%Y-%m-%d")
    dest_root = ARCHIVE_ROOT / stamp
    print(f"Archive: {dest_root}")
    print(f"Files to move: {len(rels)}  ({hsize(total)})\n")

    for rel in rels:
        print(f"  {rel.as_posix()}")

    if not apply:
        print(f"\n=== PREVIEW only. {len(rels)} files, {hsize(total)} would move. ===")
        print("Re-run with --apply to actually move them.")
        return 0

    if not rels:
        print("Nothing to move.")
        return 0

    dest_root.mkdir(parents=True, exist_ok=True)
    manifest = dest_root / "_RESTORE_MANIFEST.tsv"
    restore_bat = dest_root / f"restore_{stamp}.bat"
    moved = 0
    with open(manifest, "w", encoding="utf-8") as mf, \
         open(restore_bat, "w", encoding="utf-8") as rb:
        mf.write("original_path\tarchived_path\n")
        rb.write("@echo off\r\n")
        rb.write("REM Restore everything archived in this batch back to the repo.\r\n")
        for rel in rels:
            src = INSTALL_DIR / rel
            dst = dest_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved += 1
                mf.write(f"{src}\t{dst}\n")
                rb.write(f'if not exist "{src.parent}" mkdir "{src.parent}"\r\n')
                rb.write(f'move /Y "{dst}" "{src}"\r\n')
            except OSError as e:
                print(f"  [SKIP] {rel.as_posix()} — {e}")
        rb.write("echo Restore complete.\r\npause\r\n")

    print(f"\n=== DONE: moved {moved}/{len(rels)} files into {dest_root} ===")
    print(f"Manifest : {manifest}")
    print(f"Undo with: {restore_bat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
