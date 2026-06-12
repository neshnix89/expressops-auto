"""
FOLDER CLEANUP AUDIT — read-only.

Walks the company-laptop expressops-auto install dir and classifies every file
so we can decide what to archive. NOTHING is moved or deleted here — this only
lists. The actual move is a separate, reviewed step (folder_cleanup.py).

Classification (Windows-case-insensitive):
  PROTECTED  — secrets / EDMAdmin.exe / credentials. NEVER archive. Listed so
               we can confirm by eye they are present and untouched.
  TRACKED    — part of the GitHub repo (in the baked-in manifest below). No
               point archiving these: the next sync_from_github re-creates them.
  EXTRA      — on the laptop but NOT in the repo. These are the scratch outputs,
               logs, screenshots and scan files that pile up and never get
               cleaned. THESE are the cleanup candidates.

The manifest is `git ls-files` from the VPS at build time. Regenerate when the
repo changes materially.
"""

from __future__ import annotations

import fnmatch
import os
from datetime import datetime
from pathlib import Path

INSTALL_DIR = Path(r"C:\Users\tmoghanan\Documents\AI\expressops-auto")

# --- Files that must NEVER be archived, even though they aren't in the repo. ---
# Matched against the normalized (forward-slash, lowercase) relative path.
PROTECTED_GLOBS = (
    "config/config.yaml",
    "config.yaml",
    "credentials.json",
    "cf_config.yml",
    "*.exe",            # EDMAdmin.exe and any other renamed interpreter
    "*.pem", "*.key", "*.pfx", "*.p12",
    ".env", "*.env",
    "*.token", "token*.json", "tokens.json",  # stray token caches (not scripts)
)

# Dirs we report as a single rolled-up group rather than file-by-file.
ROLLUP_DIRS = ("__pycache__", ".git", ".idea", ".vscode")

# --- Canonical repo manifest (git ls-files on VPS main). ---
TRACKED_RAW = """
.gitignore
CLAUDE.md
Claude prompt long.txt
PROJECT_STATUS.md
README.md
bom_scanner_fix2_prompt.md
bom_scanner_fix_prompt.md
clients/__init__.py
clients/m3_h5_client.py
config/audit_rules.yaml
config/config.example.yaml
core/__init__.py
core/config_loader.py
core/confluence.py
core/edm.py
core/errors.py
core/jira_client.py
core/logger.py
core/m3.py
docs/LEGACY_REFERENCE.md
docs/M3_CONNECTIVITY_REFERENCE.md
docs/PROJECT_STATUS.md
docs/REF_EDM.md
docs/TASK_TEMPLATE.md
docs/WORKLOG.md
dry_run_output2.txt
dry_run_output3.txt
logs/bom_scanner.log
logs/bom_scanner.publish.log
logs/capture_bom_scanner.log
logs/capture_to_status_check.log
logs/container_summary.log
logs/doctor.log
logs/m3_h5_client.log
logs/mo_trigger.log
logs/mo_trigger_comment.log
logs/mo_trigger_comment.publish.log
logs/to_status_check.log
logs/to_status_check.publish.log
logs/to_status_check_bat.log
logs_bak/capture_to_status_check.log
logs_bak/to_status_check.log
m3_h5_client.py
ops.bat
ops_push.ps1
ops_push.py
outputs/mo_trigger_ACDC-1041.txt
outputs/mo_trigger_ACDC-1052.txt
outputs/mo_trigger_CAPF-2954.txt
outputs/mo_trigger_DDE4735-1912.txt
outputs/mo_trigger_EMHP-10232.txt
outputs/mo_trigger_NPIOTHER-4014.txt
outputs/mo_trigger_NPIOTHER-4231.txt
outputs/mo_trigger_NPIOTHER-4260.txt
outputs/mo_trigger_NPIOTHER-4305.txt
outputs/mo_trigger_NPIOTHER-4381.txt
outputs/mo_trigger_NPIOTHER-4600.txt
outputs/mo_trigger_NPIOTHER-4610.txt
outputs/mo_trigger_NPIOTHER-4682.txt
outputs/mo_trigger_NPIOTHER-4751.txt
outputs/mo_trigger_NPIOTHER-4752.txt
outputs/mo_trigger_NPIOTHER-4771.txt
outputs/mo_trigger_NPIOTHER-4804.txt
outputs/mo_trigger_NPIOTHER-4859.txt
outputs/mo_trigger_NPIOTHER-4917.txt
outputs/mo_trigger_OBXR100-596.txt
outputs/mo_trigger_SILED2-3268.txt
outputs/mo_trigger_SILED2-3310.txt
publish_mr_report.bat
requirements.txt
run_bom_scanner.bat
run_mo_trigger.bat
run_mr_report.bat
scripts/capture_mock_data.py
scripts/clean_config.py
scripts/close_logistics.bat
scripts/doctor.py
scripts/first_time_download.bat
scripts/fix_config_readonly.py
scripts/folder_audit.py
scripts/folder_cleanup.py
scripts/run_cleanup_apply.bat
scripts/run_cleanup_preview.bat
scripts/run_folder_audit.bat
scripts/ignore_container.bat
scripts/mo_trigger_publish.bat
scripts/probe.py
scripts/push_audit_comment.bat
scripts/readonly_guard.py
scripts/run_audit.bat
scripts/run_audit.py
scripts/run_audit_batch.bat
scripts/run_container_audit.bat
scripts/run_probe.bat
scripts/run_probe.py
scripts/set_tokens.bat
scripts/set_tokens.py
scripts/setup_edmadmin.py
scripts/setup_env.bat
scripts/sync_now.bat
scripts/show_perrow_scan_results.py
scripts/show_status.py
scripts/sync_from_github.py
scripts/tableau_discovery.py
scripts/tableau_ds_probe.py
scripts/tableau_find_perrow_views.py
scripts/tableau_lookup_owner.py
scripts/tee_run.py
scripts/to_status_check_run.bat
setup_edmadmin.bat
sync_exclude.txt
tasks/__init__.py
tasks/bom_scanner/TASK.md
tasks/bom_scanner/__init__.py
tasks/bom_scanner/bom_scanner_handoff.md
tasks/bom_scanner/capture.py
tasks/bom_scanner/logic.py
tasks/bom_scanner/main.py
tasks/bom_scanner/publish.py
tasks/container_summary/WORKLOG.md
tasks/container_summary/__init__.py
tasks/container_summary/cache.py
tasks/container_summary/capture.py
tasks/container_summary/container_summary_TASK.md
tasks/container_summary/discover.py
tasks/container_summary/last_run.json
tasks/container_summary/llm.py
tasks/container_summary/logic.py
tasks/container_summary/main.py
tasks/container_summary/mock_data/children_LCUSAMB-1755.json
tasks/container_summary/mock_data/children_NPIOTHER-3902.json
tasks/container_summary/mock_data/children_NPIOTHER-4085.json
tasks/container_summary/mock_data/issue_LCUSAMB-1755.json
tasks/container_summary/mock_data/issue_NPIOTHER-3902.json
tasks/container_summary/mock_data/issue_NPIOTHER-4085.json
tasks/container_template_audit/AUDIT_RULES_README.md
tasks/container_template_audit/batch.py
tasks/container_template_audit/comment_push.py
tasks/container_template_audit/ignore.py
tasks/container_template_audit/main.py
tasks/container_template_audit/mock_data/issue_OBXR100-690.json
tasks/container_template_audit/mock_data/issue_OBXR200-100.json
tasks/container_template_audit/mock_data/search_issue_in_relation__OBXR100_690____Project_Children___Tasks__Deviations__level1_.json
tasks/container_template_audit/mock_data/search_issue_in_relation__OBXR200_100____Project_Children___Tasks__Deviations__level1_.json
tasks/gdc_transfer_check/gdc_transfer_check.py
tasks/mo_trigger_comment/TASK.md
tasks/mo_trigger_comment/__init__.py
tasks/mo_trigger_comment/capture.py
tasks/mo_trigger_comment/logic.py
tasks/mo_trigger_comment/m3_checks.py
tasks/mo_trigger_comment/main.py
tasks/mo_trigger_comment/publish.py
tasks/mr_status_report/TASK.md
tasks/mr_status_report/__init__.py
tasks/mr_status_report/main.py
tasks/to_status_check/TASK.md
tasks/to_status_check/__init__.py
tasks/to_status_check/capture.py
tasks/to_status_check/capture_m3.py
tasks/to_status_check/logic.py
tasks/to_status_check/main.py
tasks/to_status_check/publish.py
tasks/to_status_check/test_phase_b.py
test_ecx450_debug.py
test_ecx450_enter.py
test_ecx450_expect.py
test_ecx450_final.py
test_ecx450_nav.py
test_ecx450_nav10.py
test_ecx450_nav3.py
test_ecx450_nav4.py
test_ecx450_nav5.py
test_ecx450_nav6.py
test_ecx450_nav7.py
test_ecx450_nav8.py
test_ecx450_nav9.py
test_ecx450_poll.py
test_jql.py
test_jql2.py
test_jql3.py
test_jql4.py
update_docs_prompt.md
"""

TRACKED = {p.strip().lower() for p in TRACKED_RAW.splitlines() if p.strip()}


def norm(rel: Path) -> str:
    return rel.as_posix().lower()


def is_protected(rel_norm: str) -> bool:
    base = rel_norm.rsplit("/", 1)[-1]
    for g in PROTECTED_GLOBS:
        if fnmatch.fnmatch(rel_norm, g) or fnmatch.fnmatch(base, g):
            return True
    return False


def hsize(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:,.1f}{unit}"
        f /= 1024
    return f"{f:,.1f}GB"


def mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "????-??-??"


def main() -> None:
    print(f"=== FOLDER CLEANUP AUDIT (READ-ONLY) ===")
    print(f"Target: {INSTALL_DIR}")
    if not INSTALL_DIR.is_dir():
        print("  ERROR: install dir not found. Are you on the company laptop?")
        return

    protected, tracked, extra = [], [], []
    rollup: dict[str, list[int]] = {}  # rollup dir -> [count, bytes]

    for dirpath, dirnames, filenames in os.walk(INSTALL_DIR):
        d = Path(dirpath)
        # roll up noisy regenerable dirs
        parts_lower = {seg.lower() for seg in d.relative_to(INSTALL_DIR).parts}
        roll_hit = next((r for r in ROLLUP_DIRS if r in parts_lower), None)
        if roll_hit:
            for fn in filenames:
                try:
                    sz = (d / fn).stat().st_size
                except OSError:
                    sz = 0
                slot = rollup.setdefault(roll_hit, [0, 0])
                slot[0] += 1
                slot[1] += sz
            continue
        for fn in filenames:
            p = d / fn
            rel = p.relative_to(INSTALL_DIR)
            rn = norm(rel)
            try:
                sz = p.stat().st_size
            except OSError:
                sz = 0
            rec = (rel.as_posix(), sz, mtime(p))
            if is_protected(rn):
                protected.append(rec)
            elif rn in TRACKED:
                tracked.append(rec)
            else:
                extra.append(rec)

    print(f"\n--- PROTECTED (never archive; confirming present) — {len(protected)} ---")
    for rel, sz, mt in sorted(protected):
        print(f"  [KEEP] {rel}  ({hsize(sz)}, {mt})")
    if not protected:
        print("  (none found)")

    print(f"\n--- REGENERABLE DIRS (rolled up; safe to delete, auto-recreated) ---")
    if rollup:
        for r, (cnt, by) in sorted(rollup.items()):
            print(f"  {r}/ : {cnt} files, {hsize(by)}")
    else:
        print("  (none)")

    print(f"\n--- TRACKED repo files present — {len(tracked)} "
          f"(leave alone; sync re-creates them) ---")
    print(f"  (not listed individually; {len(TRACKED)} in manifest)")
    # Note any tracked manifest files MISSING on the laptop (informational).
    present = {norm(Path(r)) for r, _, _ in tracked}
    missing = sorted(TRACKED - present)
    if missing:
        print(f"  NOTE: {len(missing)} manifest files not present on laptop "
              f"(e.g. {', '.join(missing[:5])}{'...' if len(missing)>5 else ''})")

    print(f"\n--- EXTRA / UNTRACKED — CLEANUP CANDIDATES — {len(extra)} ---")
    total = sum(sz for _, sz, _ in extra)
    # group by top-level dir for readability
    by_dir: dict[str, list[tuple[str, int, str]]] = {}
    for rel, sz, mt in extra:
        top = rel.split("/", 1)[0] if "/" in rel else "(root)"
        by_dir.setdefault(top, []).append((rel, sz, mt))
    for top in sorted(by_dir):
        items = sorted(by_dir[top])
        sub = sum(s for _, s, _ in items)
        print(f"\n  [{top}]  ({len(items)} files, {hsize(sub)})")
        for rel, sz, mt in items:
            print(f"     {rel}  ({hsize(sz)}, {mt})")
    print(f"\n=== CANDIDATE TOTAL: {len(extra)} files, {hsize(total)} ===")
    print("Nothing has been moved. Paste this back; we'll pick what to archive.")


if __name__ == "__main__":
    main()
