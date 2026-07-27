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

## PE / TE report release colouring
The **PE Reports** and **TE Reports** cells are coloured per report number from
EDM: **green** = released, **red** = not released, not found, or no document.

Unlike PRSG (reached indirectly via `EDM_REFERENCES.REF` = the PT number), these
report numbers ARE `EDM_DOCS.DOCNUMBER` values, so it is a direct lookup —
confirmed by `scripts/probe_edm_reports.py` against the live page (30/30 exact
matches). `RELEASESTATE` is a **string**; observed values across QD/906 docs are
`'9'` released (16.6k), `'0'` (17.7k), `'5'` (1.0k), `'4'` (101). `9 == Released`,
same coding as PRSG.

Revisions are separate documents with separate states (`906-0011` = 5 while
`906-0011A` = 9), so numbers are matched **exactly** and revisions are never
collapsed — unlike the Handover PT matching, which is deliberately
revision-tolerant.

Colouring is **per number, not per cell**: a cell often holds several reports and
one unreleased among released ones is the case worth seeing. If the EDM lookup
fails, cells render as plain uncoloured text rather than painting everything red.
Only the active tables are coloured; COMPLETED MR keeps plain text, since those
rows are replayed from the page and would lose the markup on the next round trip.

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
Use **plain python** — `EDMClient` spawns `EDMAdmin.exe` as a subprocess for the
Oracle call, so it is not the entry point:
```
# Preview (reads live, builds page, does NOT publish):
...\Python312\python.exe -m tasks.mr_status_report.main --live --dry-run
# Publish for real:
...\Python312\python.exe -m tasks.mr_status_report.main --live
```
Running `EDMAdmin.exe` directly from a shell fails silently with exit
`0xC0000135` (STATUS_DLL_NOT_FOUND): it is a bare copy of `python.exe` with no
`python312.dll` beside it, and Python is not on PATH on that laptop.
One-click: `run_mr_report.bat` (dry-run) / `publish_mr_report.bat` (live).
`--mock` is a no-op (live-only task; no saved mock data).

## Publish guards (do not remove)
The Confluence page — not this script — is the source of truth for the COMPLETED
MR history, the manual MR Status / Remarks columns and both tick-boxes. If the
page read fails, none of that is in hand, so the run **refuses to publish**
(`--allow-stale-page` overrides; never use it on the scheduled run). Likewise
`conf_update` never retries a 409 when `version == 0`, because version 0 means
the page was never read and the 409 is Confluence correctly rejecting the write.

Both guards exist because of the **2026-07-23 13:07 incident**: a transient
connection error on the page GET was swallowed, the run rebuilt the page from
nothing, Confluence rejected it with a 409, and the retry path re-fetched the
real version and forced it through as v254 — dropping 28 completed containers
back into Active, clearing all 4 "MR in progress" ticks and blanking every
"MR Week XX" remark (which is what made the MR Week Schedule table disappear).

## Discovery / to confirm on the laptop
- EDMAdmin.exe path (config `edm.python_exe`, default
  `C:\Users\tmoghanan\EDMAdmin.exe`) — the bats fall back to plain python if
  absent (EDM/PRSG then skipped).
- Existing Task Scheduler job that runs the old standalone daily ~10:00 — to be
  re-pointed to this module via a one-click schtasks bat once verified.
