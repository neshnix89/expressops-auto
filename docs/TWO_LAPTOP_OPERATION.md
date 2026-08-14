# Two-laptop operation (active/active)

**Both laptops run everything, all the time.** Either one covers when the other
is off, asleep, on leave or broken — no handover step, nothing to remember
before going away.

This replaces the earlier one-machine-at-a-time rule.

| | Primary | Second |
|---|---|---|
| Windows account | `TMOGHANAN` | `WNEO` |
| Install path | `C:\Users\tmoghanan\Documents\AI\expressops-auto` | `C:\Users\wneo\Documents\AI\expressops-auto` |

---

## How both can run safely

Set `shared_dir` to the SAME network folder in both `config.yaml` files. Then:

* **A run lock** — before writing, each task atomically creates
  `<shared_dir>\locks\<task>.lock`. Exactly one machine wins; the other logs
  "the other laptop is covering it" and exits cleanly. A lock older than
  `run_lock_ttl_minutes` is treated as abandoned and taken over, so a laptop
  closed mid-run cannot wedge the schedule.
* **Shared state** — `mo_ref_order_monitor` already keeps its history and alert
  queue there. The costing task's go-live baseline now lives there too, so
  seeding on one laptop switches both on.
* **JIRA as the shared record** — the costing task recognises its own
  `#Ref: CostHS-Trigger#` footers in the comments, which every machine sees.

If the shared folder is unreachable, tasks run **unlocked** and say so in the
log. That is deliberate: a missed report is worse than a rare double-write, and
a machine that cannot see the share cannot coordinate anyway.

Schedules stay offset by ~15 minutes regardless — the lock handles overlap, but
not overlapping is cheaper than resolving it.

---

## Schedules

Primary (existing), and the second laptop offset by +15 minutes so the two never
run in the same minute:

| Task | Primary | Second | Both run? |
|---|---|---|---|
| `MO_RefOrder_Monitor` | 08:00, every 30 min | 09:15, every 30 min | Yes — shared state |
| `CostingHSCode` | 09:45, 13:00, 16:15 | 10:00, 13:15, 16:30 | Yes — locked |
| `MR_Status_Report` | 10:00 | 10:15 | Yes — locked |
| `KPI_Overlay` (repo) | 09:30 | 09:45 | Yes — locked |
| `LiveKPI_Daily` (legacy) | 09:28 | — not portable — | Retire once the repo version is verified |
| `ExpressOPS KPI Weekly` (legacy) | Mon 10:00 | — | **Retired — Tableau replaces it** |

---

## The two legacy KPI jobs

`LiveKPI_Daily` and `ExpressOPS KPI Weekly` run from folders OUTSIDE this repo:

```
C:\Users\tmoghanan\Documents\AI\LiveKPI_Overlay\run_live_kpi.bat
C:\Users\tmoghanan\Documents\AI\ExpressOPS_KPI\run_kpi.bat
```

They cannot be installed on the second laptop from this repo.

* **The daily overlay has a migrated replacement** — `tasks/kpi_overlay`, which
  also covers Trutnov. Verify it, then run the repo version and retire the
  legacy one.
* **The weekly KPI pipeline is retired** — Tableau covers it now. Disable
  `ExpressOPS KPI Weekly` on the primary; nothing needs to replace it.

---

## One-time setup — on the PRIMARY

```powershell
cd C:\Users\tmoghanan\Documents\AI\expressops-auto

# 1. Turn on active/active: add to config.yaml (single quotes, literal backslashes)
#    shared_dir: 'Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering'
#    run_lock_ttl_minutes: 20

# 2. Move the go-live baseline onto the share so BOTH machines honour it.
copy outputs\costing_hs_code_trigger_baseline.json "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\"

# 3. Verify the repo KPI overlay before retiring the legacy one
#    (read-only: computes everything, uploads nothing)
python -m tasks.kpi_overlay.main --live --dry-run

# 4. One overlay only — the repo version replaces the legacy one
Enable-ScheduledTask  -TaskName "ExpressOps KPI Overlay"
Disable-ScheduledTask -TaskName LiveKPI_Daily

# 5. Retired: Tableau covers this now
Disable-ScheduledTask -TaskName "ExpressOPS KPI Weekly"
```

**Nothing to disable before leave.** Both machines keep running; the lock
decides who does the work each slot.

Confirm nothing was missed:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match 'KPI|MR|Costing|MO_Ref' } |
  ForEach-Object { "{0,-28} {1}" -f $_.TaskName, $_.State }
```

---

## On the SECOND laptop

### Refresh config.yaml FIRST

A config written by an earlier build of the installer has **no Confluence PAT,
no `pages` block and no `costing_hs_code_trigger` block** — so the MR report and
the KPI overlay cannot publish and the costing task tags nobody. The installer
leaves an existing config.yaml alone, so it must be told to rewrite it:

```powershell
powershell -ExecutionPolicy Bypass -File "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\_install\expressops-auto\scripts\install_second_laptop.ps1" -FromPath "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\_install\expressops-auto" -FleetWide -Force
```

It prompts for the JIRA PAT (masked), the Webex space link, and the Confluence
PAT. The dry run at the end still fails on M3 until access is granted — that is
expected, and it correctly refuses to schedule the MO monitor.

### Then

```powershell
cd C:\Users\wneo\Documents\AI\expressops-auto

# Prove what this machine can actually reach first.
.\check_access.bat

# EDM is a SETUP step, not an access request: the Oracle logon trigger rejects
# connections by program name, so Python needs EDMAdmin.exe even though the
# account is fine via SSO.
.\setup_edmadmin.bat

.\scripts\setup_schedule.ps1 -TaskName CostingHSCode   -Runner run_costing_hs_code_trigger.bat -AtTimes "10:00","13:15","16:30"
.\scripts\setup_schedule.ps1 -TaskName MR_Status_Report -Runner scheduled_mr_publish.bat        -AtTimes "10:15"
.\scripts\setup_schedule.ps1 -TaskName KPI_Overlay      -Runner scheduled_kpi_overlay.bat       -AtTimes "09:45"
```

`MO_RefOrder_Monitor` is registered by the installer at 09:15 and stays
blocked until M3 access is granted — the dry run fails and the installer
refuses to schedule it, which is correct.

### First run of the costing task on a new machine

With `shared_dir` set, the machine reads the baseline the primary already
seeded — nothing to do. **Without it**, its baseline starts empty and the first
run comments on every container that is already ready, so seed it first:

```powershell
python -m tasks.costing_hs_code_trigger.main --live --seed-baseline
```

Confirm which file it is using — the run logs `baseline file: <path>`. A path
under `outputs\` means this machine is NOT sharing the baseline.

---

## Daily checks while covering

```powershell
# what ran, and did it succeed (result=0)
Get-ScheduledTask | Where-Object { $_.TaskName -match 'KPI|MR|Costing|MO_Ref' } |
  ForEach-Object { $i = Get-ScheduledTaskInfo $_.TaskName
    "{0,-24} last={1} result={2}" -f $_.TaskName, $i.LastRunTime, $i.LastTaskResult }
```

Logs live in `logs\`. For the MO monitor, `attempt 1: MATCH` in
`mo_ref_order_monitor_run.log` means Webex delivery is healthy; repeated
`attempt 2`/`3` means raise `open_delay_seconds` in `config.yaml`.

**JIRA is always the source of truth.** If a Webex alert looks missing, check
the container before assuming the MO has not moved.

---

## On return

Nothing. Both machines have been running the whole time.

## Who did what

Every task logs which machine took the lock. To see what is held right now:

```powershell
python -c "import sys;sys.path.insert(0,'.');from core.runlock import lock_status;from core.config_loader import load_config;print(chr(10).join(lock_status(load_config().get('shared_dir','')) or ['none held']))"
```

`check_access.bat` reports the same thing under section 5b, along with whether
the shared folder is reachable at all.
