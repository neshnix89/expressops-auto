# PROJECT STATUS

> Last updated: 2026-04-18
> Updated by: Task 1 Phase A build session

---

## Overview

Modular automation for Express Operations NPI at Pepperl+Fuchs Singapore.
Building task-by-task, testing each one, then consolidating into a dashboard later.
Replaces ALMA-T (shelved — unstructured, no logic separation).

Coworker going on long HL leave — workload doubles from ~20 containers to ~40.
Priority: automate the repetitive checks first, write operations later.

---

## Environment

| Component | Location | Purpose |
|-----------|----------|---------|
| VPS (Windows) | Claude Code + Git repo | Development, code writing, mock testing |
| GitHub | github.com/neshnix89/expressops-auto (private) | Bridge between VPS and company laptop |
| Company Laptop | C:\Users\tmoghanan\Documents\AI\expressops-auto | Live execution, data capture, Task Scheduler |
| ops.bat | Company laptop CLI | sync, test, run, capture — no Git needed, uses PowerShell download |

---

## Task Registry

| # | Task Name | Category | Status | Systems | Risk Level |
|---|-----------|----------|--------|---------|------------|
| 1 | to_status_check | General | **Phase A ✓ / Phase B pending** | JIRA + M3 | Low (read-only) |
| 2 | imr_creation | General | Backlog | M3 (write) + JIRA (write) | HIGH |
| 3 | bom_new_component_check | General | Backlog | M3 + JIRA | Low (read-only) |
| 4 | ic_npi_container_check | General | Backlog | JIRA | Low (read-only) |
| 5 | prototype_run_check | General | Backlog | JIRA | Low (read-only) |
| 6 | container_type_mismatch | General | Backlog | JIRA | Low (read-only) |
| 7 | pilot_qty_check | General | Backlog | JIRA | Low (read-only) |
| 8 | bom_routing_edm_check | MR | Backlog | M3 + EDM + JIRA | Low (read-only) |
| 9 | mr_handover_doc | MR | Backlog | JIRA + Confluence | Medium (write) |
| 10 | ewa_plan_check | Clocking | Backlog | Unknown system | Low (read-only) |

### Priority Order (suggested)
Start with read-only checks that exercise core clients, build confidence:
1. **to_status_check** — JIRA + M3, clear pass/fail logic
2. **pilot_qty_check** — JIRA only, simple number check
3. **container_type_mismatch** — JIRA only, string comparison
4. **bom_new_component_check** — M3 + JIRA, more complex query
5. Remaining read-only tasks
6. Write tasks (imr_creation, mr_handover_doc) last

---

## Completed Tasks

### Task 1 — to_status_check, Phase A (JIRA extraction) — ✓ 2026-04-18
- Pulls active Work Containers via
  `issuetype = "Work Container" AND "Order Type" is not EMPTY AND status != Closed`
  (multi-project; no `project = EXPRESSOPS` assumption).
- For each container, fetches the issue with comments and extracts the TO
  number from the latest `TO: <digits>` comment using `TO:\s*(\d+)`.
- First live run: **13 of 200 active containers have a TO number** in a
  comment. The other 187 are correctly categorized as "No TO".
- Mock mode skips containers whose `issue_<KEY>.json` isn't in `mock_data/`
  (capture.py snapshots ~10 samples).
- Deliverables: `tasks/to_status_check/{main.py, logic.py, capture.py}`.

**Phase B (M3 status lookup) is still blocked on discovery** — see below.

---

## Discovery Log

Findings from data exploration on company laptop. Append here as things are discovered.

### JIRA Fields

Custom field IDs below were confirmed against the live JIRA field API
(2026-04-18). Full details are in `CLAUDE.md` and `docs/LEGACY_REFERENCE.md`.

| Field | Custom Field ID | Confirmed? |
|-------|----------------|------------|
| EDM Document Number | customfield_13300 | Yes |
| M3 Article Number | customfield_13502 | Yes |
| Project Status | customfield_13700 | Yes |
| Request Type | customfield_13903 | Yes |
| Product Type | customfield_13904 | Yes |
| Order Type | customfield_13905 | Yes |
| NPI Location | customfield_13906 | Yes |
| PTxx Document (**not** "Project ID") | customfield_13907 | Yes |
| Work Container NPI Status Light | customfield_15009 | Yes |
| NPI WC Status | customfield_15400 | Yes |
| Issue_parked_log (was mis-labelled "ParkingLog") | customfield_15800 | Yes |
| Component Part Number | customfield_15805 | Yes |
| TO Number | Not a field — stored in container comment body (`TO:\s*(\d+)`; latest comment wins) | Yes (2026-04-18) |

**Still unresolved** (seen in legacy `kpi_core` symbols, no IDs yet):
`Project ID` (legacy `CF_PROJECT_ID` — 13907 turned out to be PTxx Document,
not this), `Aggregated Progress` (legacy `CF_AGG_PROGRESS`).

### M3 Tables (PFODS schema, _AP suffix)
| Purpose | Table | Key Columns | Confirmed? |
|---------|-------|-------------|------------|
| Item master | MITMAS_AP | ? | Partial |
| MO header | MPDHED_AP | ? | Partial |
| MO operations | MPDOPE_AP | ? | Partial |
| TO status | ? | ? | **NEEDS DISCOVERY** |
| IMR data | ? | ? | **NEEDS DISCOVERY** |
| BOM data | ? | ? | **NEEDS DISCOVERY** |

### EDM Tables (ADMEDP schema)
| Purpose | Table | Key Columns | Confirmed? |
|---------|-------|-------------|------------|
| Document references | ADMEDP.EDM_REFERENCES | PRSG, PT, RELEASESTATE | Yes |

---

## Decisions Made

1. **2026-04-18:** Shelved ALMA-T. Building modular tasks instead.
2. **2026-04-18:** No Git on company laptop. ops sync uses PowerShell to download from GitHub.
3. **2026-04-18:** Consolidate at presentation layer (dashboard), not via unified chat agent.
4. **2026-04-18:** Each task gets TASK.md with Discovery section for unknowns.
5. **2026-04-18:** Read-only tasks first, write tasks later.

---

## Existing Automations (Pre-framework, running independently)

- **MR Status Report** — Daily 10:00 AM, Confluence page 560866215
- **ExpressOPS KPI Pipeline** — Monday 10:00 AM, Confluence page 560871424

These may be migrated into the framework later.
