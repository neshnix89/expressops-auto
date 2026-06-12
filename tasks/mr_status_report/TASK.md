# MR Status Report (Pilot Run & DMR)

## What
Daily Manufacturing-Readiness tracking report for SMT PCBA NPI containers in
Singapore. Pulls containers from JIRA, classifies each as **Pilot Run** or
**DMR Request**, cross-references EDM for PRSG release status, and publishes a
live table to Confluence page **560866215** (plus a local Excel backup).

Migrated from the standalone `C:\Users\tmoghanan\Documents\AI\MR Status Report\
Pilot_DMR_Report.py`. Logic is preserved; secrets now come from `config.yaml`.

## Systems
- **JIRA** (read): search SMT-PCBA/Singapore containers, walk `relation()`
  children, read comments for PE/TE report numbers.
- **EDM Oracle** (read): map PT number → PRSG doc + RELEASESTATE. Requires
  running under **EDMAdmin.exe** (renamed python.exe) to bypass the logon
  trigger; under plain python the EDM step is skipped gracefully.
- **Confluence** (read + write): read the page first to preserve manual columns
  (Handover PE/TE, MR Status, Remarks) and the COMPLETED MR history, then
  republish.

## Key fields / logic
- Tag field `customfield_13905` (Order Type) → Pilot Run / DMR classification.
- Pilot Run ageing from SMT Build resolution date; DMR ageing from created date.
- A container moves to **COMPLETED MR** when ANY of:
  1. PRSG is **Released** (auto), or
  2. manual **MR Status** column = DONE, or
  3. **NEW:** its **Status** tick-box on the page is ticked — the manual
     "settle" path for projects that don't need to go for MR.

## Status tick-box round-trip
- `build_html` appends a **Status** column (Confluence `<ac:task-list>`
  checkbox) to the Active MR and MR Week tables.
- `parse_ticked_containers` re-reads the raw page HTML on the next run; any
  `<tr>` containing `<ac:task-status>complete</ac:task-status>` plus a
  `/browse/<KEY>` link marks that container done. Position-independent, so it
  works whether the box was ticked in the Active or MR Week table.

## Run
```
# Preview (reads live, builds page, does NOT publish):
EDMAdmin.exe -m tasks.mr_status_report.main --live --dry-run
# Publish for real:
EDMAdmin.exe -m tasks.mr_status_report.main --live
```
One-click: `run_mr_report.bat` (dry-run) / `publish_mr_report.bat` (live).
`--mock` is a no-op (live-only task; no saved mock data).

## Discovery / to confirm on the laptop
- EDMAdmin.exe path (config `edm.python_exe`, default
  `C:\Users\tmoghanan\EDMAdmin.exe`) — the bats fall back to plain python if
  absent (EDM/PRSG then skipped).
- Existing Task Scheduler job that runs the old standalone daily ~10:00 — to be
  re-pointed to this module via a one-click schtasks bat once verified.
