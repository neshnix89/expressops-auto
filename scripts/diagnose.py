"""
One-shot health report for mo_ref_order_monitor.

Answers, in one place, the questions that otherwise need a dozen manual
commands: is the code current, is config.yaml actually being read the way you
think, is the shared state reachable, and is the scheduled task configured
correctly.

Secrets are NEVER printed — tokens and the Webex space link are reported as
"SET (n chars)" only.

Run via diagnose.bat (double-click), or:
    python scripts\\diagnose.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEP = "=" * 72


def head(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")


def redact(v) -> str:
    if v is None:
        return "MISSING"
    s = str(v)
    if not s.strip():
        return "EMPTY"
    return f"SET ({len(s)} chars)"


# Markers that tell us which changes are present in this checkout. Cheaper and
# more honest than a version string nobody remembers to bump.
CODE_MARKERS = [
    ("pilot_containers support", "tasks/mo_ref_order_monitor/main.py", "pilot_containers"),
    ("fleet-wide warning", "tasks/mo_ref_order_monitor/main.py", "FLEET-WIDE"),
    ("--map flag", "tasks/mo_ref_order_monitor/main.py", '"--map"'),
    ("--reset flag", "tasks/mo_ref_order_monitor/main.py", '"--reset"'),
    ("No Status label", "tasks/mo_ref_order_monitor/main.py", "no_status_label"),
    ("dry-run keeps queue clean", "tasks/mo_ref_order_monitor/webex.py", "must not write the shared queue"),
    ("shared/absolute paths", "tasks/mo_ref_order_monitor/main.py", "def _resolve"),
    ("chat-window targeting", "tasks/mo_ref_order_monitor/send_webex_desktop.ps1", "Get-WebexChatWindow"),
    # 13-Aug: focus alone was never proof the text got into the compose box.
    # Two alerts posted nothing while the log said "sent". If these two read
    # OLD, that hole is still open on this machine.
    ("compose-box read-back", "tasks/mo_ref_order_monitor/send_webex_desktop.ps1",
     "compose box never held the message"),
    ("paste (no SendKeys escaping)", "tasks/mo_ref_order_monitor/webex.py", "RAW text"),
    # 14-Aug: MO 7003944044 went AOI-IS -> PACK-IS and alerted nothing, because
    # IS was a single on/off latch and it was already on. If this reads OLD, an
    # issue moving between stages is still silent on this machine.
    ("per-stage issue tracking", "tasks/mo_ref_order_monitor/logic.py", "issue_moved"),
]


def main() -> int:
    print(f"mo_ref_order_monitor diagnostics — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}  ({sys.executable})")

    # ── code freshness ───────────────────────────────────────────────
    head("1. CODE VERSION (is this checkout up to date?)")
    missing = 0
    for label, rel, needle in CODE_MARKERS:
        p = ROOT / rel
        if not p.exists():
            print(f"  [MISSING FILE] {label:28} {rel}")
            missing += 1
            continue
        ok = needle in p.read_text(encoding="utf-8", errors="replace")
        print(f"  [{'OK ' if ok else 'OLD'}] {label:28} ({rel})")
        missing += 0 if ok else 1
    if missing:
        print(f"\n  >>> {missing} item(s) OLD/MISSING — run sync_now.bat, then re-run this.")
    else:
        print("\n  All expected changes present.")

    mainpy = ROOT / "tasks/mo_ref_order_monitor/main.py"
    if mainpy.exists():
        ts = datetime.fromtimestamp(mainpy.stat().st_mtime)
        print(f"  main.py last modified: {ts:%Y-%m-%d %H:%M:%S}")

    # ── config ───────────────────────────────────────────────────────
    head("2. CONFIG (what the program actually reads)")
    cfg_path = ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        print(f"  MISSING: {cfg_path}")
        return 1
    raw = cfg_path.read_text(encoding="utf-8-sig", errors="replace")

    # Duplicate top-level blocks are silent: PyYAML keeps only the LAST one,
    # so an edit to an earlier copy appears to do nothing.
    dupes = re.findall(r"^mo_ref_order_monitor:", raw, re.M)
    print(f"  'mo_ref_order_monitor:' blocks found: {len(dupes)}")
    if len(dupes) > 1:
        print("  >>> DUPLICATE BLOCKS. YAML keeps only the LAST one — edits to an")
        print("      earlier copy are ignored. Merge them into a single block.")

    try:
        import yaml
        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"  YAML PARSE ERROR: {exc}")
        return 1

    mo = data.get("mo_ref_order_monitor") or {}
    wx = mo.get("webex") or {}
    print(f"  jira.pat            : {redact((data.get('jira') or {}).get('pat'))}")
    print(f"  pilot_containers    : {mo.get('pilot_containers', 'MISSING')!r}")
    print(f"  state_dir           : {mo.get('state_dir', 'MISSING')!r}")
    print(f"  no_status_label     : {mo.get('no_status_label', 'MISSING')!r}")
    print(f"  issue_regex         : {mo.get('issue_regex', 'MISSING')!r}")
    print(f"  webex.enabled       : {wx.get('enabled', 'MISSING')!r}")
    print(f"  webex.transport     : {wx.get('transport', 'MISSING')!r}")
    print(f"  webex.space_link    : {redact(wx.get('space_link'))}")
    print(f"  webex.queue_file    : {wx.get('queue_file', 'MISSING')!r}")
    print(f"  webex.open_delay    : {wx.get('open_delay_seconds', 'MISSING')!r}")
    print(f"  webex.type_delay    : {wx.get('type_delay_seconds', 'MISSING')!r}")

    if not mo.get("pilot_containers"):
        print("\n  >>> pilot_containers empty/absent -> runs FLEET-WIDE (every")
        print("      container in the JQL), not just the pilot ones.")

    # ── shared state ─────────────────────────────────────────────────
    head("3. STATE + QUEUE (shared between laptops?)")
    for label, val in (("state_dir", mo.get("state_dir")),
                       ("queue_file", wx.get("queue_file"))):
        if not val:
            print(f"  {label}: NOT CONFIGURED")
            continue
        p = Path(val)
        if not p.is_absolute():
            p = ROOT / p
            print(f"  {label}: RELATIVE -> local only ({p})")
        else:
            print(f"  {label}: {p}")
        if label == "state_dir":
            if p.exists():
                files = sorted(p.glob("state_*.json"))
                print(f"     exists, {len(files)} state file(s)")
                for f in files[:10]:
                    ts = datetime.fromtimestamp(f.stat().st_mtime)
                    print(f"       {f.name:34} {ts:%Y-%m-%d %H:%M}")
            else:
                print("     >>> DOES NOT EXIST / not reachable (is Y: mapped?)")
        else:
            print(f"     {'exists' if p.exists() else 'not created yet (normal if no alert pending)'}")

    # ── scheduled task ───────────────────────────────────────────────
    head("4. SCHEDULED TASK")
    ps = (
        "$t=Get-ScheduledTask -TaskName 'MO_RefOrder_Monitor' -ErrorAction SilentlyContinue;"
        "if(-not $t){'NOT REGISTERED'}else{"
        "$i=Get-ScheduledTaskInfo -TaskName 'MO_RefOrder_Monitor';"
        "'state            : '+$t.State;"
        "'action           : '+$t.Actions.Execute;"
        "'last run         : '+$i.LastRunTime+'  result='+$i.LastTaskResult;"
        "'next run         : '+$i.NextRunTime;"
        "'runs on battery  : '+(-not $t.Settings.DisallowStartIfOnBatteries);"
        "'catches up missed: '+$t.Settings.StartWhenAvailable}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=60, check=False,
        )
        text = (out.stdout or b"").decode("utf-8", "replace").strip()
        print("  " + (text.replace("\n", "\n  ") if text else "(no output)"))
    except Exception as exc:  # noqa: BLE001
        print(f"  could not query Task Scheduler: {exc}")

    head("5. DRY RUN (read-only — nothing is written)")
    print("  see below…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
