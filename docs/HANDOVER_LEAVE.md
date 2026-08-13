# Leave handover — running the automations from the second laptop

Written for the week TM is away. Two machines exist; the safe arrangement is
**one machine runs each task**, not both.

| | Primary | Second |
|---|---|---|
| Windows account | `TMOGHANAN` | `WNEO` |
| Install path | `C:\Users\tmoghanan\Documents\AI\expressops-auto` | `C:\Users\wneo\Documents\AI\expressops-auto` |

---

## Why not simply run both

Only `mo_ref_order_monitor` was built for two machines: its history and alert
queue live on the shared Y: drive, so whichever laptop polls first sees what the
other did.

**Every other task keeps its state locally.** Two machines running
`costing_hs_code_trigger` means each has its own record of who has replied
"Done", so both post their own comments — the same people tagged twice, and a
reminder loop that never converges. `mr_status_report` and `kpi_overlay` both
republish a shared Confluence target; two writers race, and the loser's update
is silently overwritten.

Offsetting the times does not fix this. It only means the duplicate arrives
fifteen minutes later.

---

## Schedules

Primary (existing), and the second laptop offset by +15 minutes so the two never
run in the same minute:

| Task | Primary | Second | Shared state? |
|---|---|---|---|
| `MO_RefOrder_Monitor` | 08:00, every 30 min | 09:15, every 30 min | **Yes — both may run** |
| `CostingHSCode` | 09:45, 13:00, 16:15 | 10:00, 13:15, 16:30 | No — one only |
| `MR_Status_Report` | 10:00 | 10:15 | No — one only |
| `KPI_Overlay` (repo) | 09:30 | 09:45 | No — one only |
| `LiveKPI_Daily` (legacy) | 09:28 | — not portable — | No — one only |
| `ExpressOPS KPI Weekly` (legacy) | Mon 10:00 | — not portable — | No — one only |

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
* **The weekly KPI pipeline has no replacement yet.** Either leave the primary
  laptop powered on with that task enabled, or accept that it does not publish
  that week. Decide deliberately — it is the one job with no handover path.

---

## Before leaving — on the PRIMARY

```powershell
cd C:\Users\tmoghanan\Documents\AI\expressops-auto

# 1. Verify the repo KPI overlay matches the legacy one BEFORE swapping.
#    (read-only: computes everything, uploads nothing)
python -m tasks.kpi_overlay.main --live --dry-run

# 2. Hand the write tasks over.
Disable-ScheduledTask -TaskName CostingHSCode_BK_0945
Disable-ScheduledTask -TaskName CostingHSCode_BK_1300
Disable-ScheduledTask -TaskName CostingHSCode_BK_1615
Disable-ScheduledTask -TaskName MR_Status_Report
Disable-ScheduledTask -TaskName LiveKPI_Daily
Disable-ScheduledTask -TaskName "ExpressOps KPI Overlay"

# 3. The weekly has no replacement — disable ONLY if the machine stays off.
# Disable-ScheduledTask -TaskName "ExpressOPS KPI Weekly"

# 4. Leave MO_RefOrder_Monitor ENABLED. It is shared-state safe.
```

Confirm nothing was missed:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match 'KPI|MR|Costing|MO_Ref' } |
  ForEach-Object { "{0,-28} {1}" -f $_.TaskName, $_.State }
```

---

## On the SECOND laptop

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

Its state starts empty, so **seed the baseline before the first scheduled run**
or it will comment on every container that is already ready:

```powershell
python -m tasks.costing_hs_code_trigger.main --live --seed-baseline
```

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

## On return — reverse it

Re-enable on the primary, disable on the second, in that order. Leave a gap of
one scheduled slot between the two so no task runs twice in the same window.

```powershell
# PRIMARY
Enable-ScheduledTask -TaskName CostingHSCode_BK_0945
Enable-ScheduledTask -TaskName CostingHSCode_BK_1300
Enable-ScheduledTask -TaskName CostingHSCode_BK_1615
Enable-ScheduledTask -TaskName MR_Status_Report
Enable-ScheduledTask -TaskName LiveKPI_Daily          # or the repo KPI_Overlay, not both

# SECOND
.\scripts\setup_schedule.ps1 -TaskName CostingHSCode    -Disable
.\scripts\setup_schedule.ps1 -TaskName MR_Status_Report -Disable
.\scripts\setup_schedule.ps1 -TaskName KPI_Overlay      -Disable
```

The costing task's local state on the second laptop records what it posted while
covering. The primary does not know about those comments — but the trigger and
reminder markers are written into the JIRA comments themselves, so the primary
reads them back and does not re-post. No reseeding needed on return.
