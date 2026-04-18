# PROJECT STATUS

> Last updated: 2026-04-18
> Updated by: Planning session (Claude.ai)

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
| 1 | to_status_check | General | **IN PROGRESS** | JIRA + M3 | Low (read-only) |
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

None yet.

---

## Discovery Log

Findings from data exploration on company laptop. Append here as things are discovered.

### JIRA Fields
| Field | Custom Field ID | Confirmed? |
|-------|----------------|------------|
| OrderType | customfield_13905 | Yes |
| Location | customfield_13906 | Yes |
| ProductType | customfield_13904 | Yes |
| RequestType | customfield_13903 | Yes |
| ParkingLog | customfield_15800 | Yes |
| TO Number | ? | **NEEDS DISCOVERY** |

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
