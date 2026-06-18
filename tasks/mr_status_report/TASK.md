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
  (MR Status, Remarks) and the COMPLETED MR history, then republish.
- **Confluence Handover pages + Comala Workflows** (read): the Handover PE/TE
  columns are auto-pulled by PT number from the PE/TE handover page trees — see
  "Handover PE/TE" below and `handover.py`.

## Key fields / logic
- Tag field `customfield_13905` (Order Type) → Pilot Run / DMR classification.
- Pilot Run ageing from SMT Build resolution date; DMR ageing from created date.
- A container moves to **COMPLETED MR** when ANY of:
  1. PRSG is **Released** (auto), or
  2. manual **MR Status** column = DONE, or
  3. its **Close container without MR** tick-box on the page is ticked — the
     manual "settle" path for projects that don't need to go for MR.

## Tick-box columns (two, both Confluence `<ac:task-list>` checkboxes)
The Active MR and MR Week tables each end with two checkbox columns:

1. **MR in progress** — tick = "ready to work on this MR". On the next run the
   container is listed in the **MR Week Schedule** table (coexists with the
   "MR Week XX" Remarks mechanism; numbered weeks first, then "In Progress").
   This box is **stateful**: it is re-rendered ticked so it persists across runs
   while the container stays active, until it is unticked or the MR is done.
2. **Close container without MR** (was "Status") — tick = settle to COMPLETED MR
   on the next run. **Momentary**: always re-rendered un-ticked, because a
   ticked container moves out of the active set anyway.

`parse_checkbox_columns` re-reads the raw page HTML. The two checkboxes are the
only `<ac:task-status>` tags in a row, so their document order maps to the
columns: `[MR in progress, Close]`. A row with a single checkbox (the old
"Status" column, on the first run after this change) is read as the Close column.
Works whether ticked in the Active or MR Week table (sets union).

## Handover PE/TE (auto-pulled from Comala workflow pages)
The **Handover PE** and **Handover TE** columns are no longer manual. Each
container's **PT number** is matched to a Confluence handover page; that page's
**Comala Document Management** workflow state (`GET /rest/cw/1/content/{id}/status`
→ `state.name`) is shown as **Approved** (final state) / **Pending** (anything
else) / **No handover** (no matching page). See `handover.py`.
- **PE** tree parent `572625450`: weekly child pages; newer weeks have one
  sub-page per PT (PT in title), older weeks list PTs in a table (fallback parses
  the table and uses the weekly page's workflow state).
- **TE** tree parent `572625454`: one child page per PT (PT in title).
- Matching is revision-tolerant (`PTDE-AXD9` ↔ `PTDE-AXD9A`). Always overwrites
  any prior manual value. Resilient: if the lookup fails the columns read
  "No handover" and publishing still proceeds.

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
