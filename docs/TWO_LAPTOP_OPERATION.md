# Two-laptop operation — step-by-step setup

**Both laptops run everything, all the time.** Either one covers when the other
is off, asleep, on leave or broken. There is no handover step and nothing to
remember before going away.

Work through this in order. Each step says what to run, what you should see,
and when to stop.

| | Primary | Second |
|---|---|---|
| Windows account | `TMOGHANAN` | `WNEO` |
| PC# | | `2201SGN733` |
| Install path | `C:\Users\tmoghanan\Documents\AI\expressops-auto` | `C:\Users\wneo\Documents\AI\expressops-auto` |
| Python | `C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe` | `C:\tools\python3\python.exe` (on PATH as `python`) |

---

## Where things stand (14-Aug-2026)

Verified on the second laptop by `check_access.bat` — 17 of 17 passed:

* M3/ODS connects, all six tables readable (IT configured the `ODSSG` DSN)
* JIRA authenticates as **his own** account, 26 containers visible
* Shared folder reachable **and writable**, 20 existing state files visible
* Webex desktop running

What is NOT done yet: his checkout predates the shared-locking work, so his
`config.yaml` has no `shared_dir`, no Confluence PAT, no `pages` block and no
costing block. Step 2 fixes all four at once.

**Interim to remember:** IT configured his DSN with the PRIMARY's Oracle
credentials. Every M3 read from his laptop is therefore attributed to
`TMOGHANAN`, and when the password policy reaches that account BOTH machines
fail together. The application account requested from IT Singapore replaces
this — see "Known expiry risk" in `tasks/mo_ref_order_monitor/TASK.md`.

---

## Step 1 — Ship the current code (PRIMARY)

```powershell
cd C:\Users\tmoghanan\Documents\AI\expressops-auto
sync_now.bat
robocopy "C:\Users\tmoghanan\Documents\AI\expressops-auto" "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\_install\expressops-auto" /MIR /XD .git logs outputs /XF config.yaml /NFL /NDL
```

`/XF config.yaml` keeps your JIRA PAT off the shared drive. robocopy exits 1 on
success (1 = files copied); only 8+ is a failure.

---

## Step 2 — Rewrite the second laptop's config (SECOND)

The installer leaves an existing config.yaml alone, so it must be told to
replace it.

```powershell
powershell -ExecutionPolicy Bypass -File "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\_install\expressops-auto\scripts\install_second_laptop.ps1" -FromPath "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\_install\expressops-auto" -FleetWide -Force
```

It prompts for four things:

| Prompt | Answer |
|---|---|
| JIRA PAT | **His own** (JIRA → Profile → Personal Access Tokens) |
| Webex space link | Webex → the space → Copy space link |
| Confluence PAT | **His own** (Confluence → Profile → Personal Access Tokens) |
| M3/ODS Oracle username | **LEAVE BLANK** — IT already configured the DSN |

Leaving the M3 fields blank is deliberate: blank means "use the DSN's own
credentials", which is exactly the setup IT just completed.

The dry run at the end should now PASS (it failed before M3 was fixed), and the
installer then registers `MO_RefOrder_Monitor` at 09:15 automatically.

**Verify:**

```powershell
cd C:\Users\wneo\Documents\AI\expressops-auto
.\check_access.bat
```

You should now see sections that were missing before: **4b Confluence**,
**4c EDM**, **5b shared run locks**. Stop here if 5b reports `shared_dir` empty
— the rest of this document depends on it.

---

## Step 3 — Turn on active/active (PRIMARY)

Add to `config\config.yaml` (single quotes so the backslashes stay literal):

```yaml
shared_dir: 'Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering'
run_lock_ttl_minutes: 20
```

Then move the costing go-live baseline onto the share, so both machines honour
one baseline instead of the second one nagging the whole backlog:

```powershell
copy outputs\costing_hs_code_trigger_baseline.json "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\"
```

**Verify:**

```powershell
.\check_access.bat
```

Section 5b should show the shared folder reachable. Locks held: `none` is
correct when nothing is running.

---

## Step 4 — MO ref tracking (SECOND)

Already scheduled by Step 2. Confirm it behaves:

```powershell
cd C:\Users\wneo\Documents\AI\expressops-auto
python -m tasks.mo_ref_order_monitor.main --live --dry-run
```

**Expect `published=0`.** Every MO is already published from the primary, so a
no-op proves the shared history is being read. If it wants to publish a full set
of tables, STOP — the state paths do not match and it would fight the primary.

```powershell
.\scripts\setup_schedule.ps1 -TaskName MO_RefOrder_Monitor -ShowOnly
```

Expect 09:15, battery-safe yes, catch-up yes.

---

## Step 5 — KPI overlay

### 5a. Decide which overlay wins (PRIMARY)

Three KPI jobs exist and two of them do the same work:

| Task | Runs | What it is |
|---|---|---|
| `LiveKPI_Daily` 09:28 | `AI\LiveKPI_Overlay\` | the ORIGINAL daily overlay |
| `ExpressOps KPI Overlay` 09:30 | `expressops-auto\` | the SAME job, migrated here, plus Trutnov |
| `ExpressOPS KPI Weekly` Mon 10:00 | `AI\ExpressOPS_KPI\` | **RETIRED — Tableau replaces it** |

```powershell
cd C:\Users\tmoghanan\Documents\AI\expressops-auto
python -m tasks.kpi_overlay.main --live --dry-run
```

Read-only — computes everything, uploads nothing. If the container and pill
counts match what the board shows today, the repo version is a faithful
replacement:

```powershell
Enable-ScheduledTask  -TaskName "ExpressOps KPI Overlay"
Disable-ScheduledTask -TaskName LiveKPI_Daily
Disable-ScheduledTask -TaskName "ExpressOPS KPI Weekly"
```

One overlay only — never both. They publish the same Confluence attachment.

### 5b. Second laptop

```powershell
cd C:\Users\wneo\Documents\AI\expressops-auto
python -m tasks.kpi_overlay.main --live --dry-run
.\scripts\setup_schedule.ps1 -TaskName KPI_Overlay -Runner scheduled_kpi_overlay.bat -AtTimes "09:45"
```

09:45 is 15 minutes after the primary's 09:30. The run lock handles overlap;
the offset means it rarely has to.

---

## Step 6 — MR status report (SECOND)

EDM is a SETUP step, not an access request: his SSO account can already see EDM,
but the Oracle logon trigger rejects connections by program name, so Python
needs `EDMAdmin.exe`.

```powershell
cd C:\Users\wneo\Documents\AI\expressops-auto
.\setup_edmadmin.bat
```

Idempotent. It creates `EDMAdmin.exe` inside the Python install directory (it
must sit beside `python3xx.dll`), writes the path into config.yaml, and verifies
with a known PT→PRSG pair.

```powershell
python -m tasks.mr_status_report.main --live --dry-run
.\scripts\setup_schedule.ps1 -TaskName MR_Status_Report -Runner scheduled_mr_publish.bat -AtTimes "10:15"
```

The dry run reads live data and builds the page **without publishing**. If EDM
is not working the PE/TE release colouring is blank — that degrades the report
rather than breaking it, so it is worth fixing but not a blocker.

---

## Step 7 — Costing / HS Code trigger (SECOND)

This one POSTS JIRA COMMENTS. Read the baseline note before scheduling it.

```powershell
cd C:\Users\wneo\Documents\AI\expressops-auto
python -m tasks.costing_hs_code_trigger.main --live --dry-run
```

Check the log line `baseline file: ...`. It MUST point at the Y: drive. If it
points under `outputs\`, `shared_dir` is not set — go back to Step 2, because
this machine would otherwise comment on the entire existing backlog.

With the shared baseline in place there is nothing to seed: it reads the one the
primary already seeded.

```powershell
.\scripts\setup_schedule.ps1 -TaskName CostingHSCode -Runner run_costing_hs_code_trigger.bat -AtTimes "10:00","13:15","16:30"
```

---

## Final schedule

| Task | Primary | Second |
|---|---|---|
| `MO_RefOrder_Monitor` | 08:00, every 30 min | 09:15, every 30 min |
| `CostingHSCode` | 09:45, 13:00, 16:15 | 10:00, 13:15, 16:30 |
| `MR_Status_Report` | 10:00 | 10:15 |
| KPI overlay | 09:30 | 09:45 |

Confirm on both machines:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match 'KPI|MR|Costing|MO_Ref' } |
  ForEach-Object { "{0,-28} {1}" -f $_.TaskName, $_.State }
```

---

## How both machines can run the same task safely

* **A run lock.** Before writing, each task atomically creates
  `<shared_dir>\locks\<task>.lock`. One machine wins; the other logs "the other
  laptop is covering it" and exits cleanly. A lock older than
  `run_lock_ttl_minutes` is treated as abandoned and taken over, so a laptop
  closed mid-run cannot wedge the schedule.
* **Shared state.** The MO monitor's history and alert queue, and the costing
  baseline, all live on the share.
* **JIRA as the shared record.** The costing task recognises its own
  `#Ref: CostHS-Trigger#` footers in the comments, which every machine sees.

If the shared folder is unreachable, tasks run **unlocked** and say so in the
log. That is deliberate: a missed report is worse than a rare double-write, and
a machine that cannot see the share cannot coordinate anyway.

---

## Daily checks

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match 'KPI|MR|Costing|MO_Ref' } |
  ForEach-Object { $i = Get-ScheduledTaskInfo $_.TaskName
    "{0,-24} last={1} result={2}" -f $_.TaskName, $i.LastRunTime, $i.LastTaskResult }
```

`result=0` is success. Logs are in `logs\`.

For the MO monitor, `attempt 1: MATCH` in `mo_ref_order_monitor_run.log` means
Webex delivery is healthy; repeated `attempt 2`/`3` means raising
`open_delay_seconds` in that machine's config.yaml.

**JIRA is always the source of truth.** If a Webex alert looks missing, check
the container before assuming the MO has not moved.
