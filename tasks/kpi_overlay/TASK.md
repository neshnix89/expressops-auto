# Task: kpi_overlay (Live JIRA Kanban KPI Overlay backend)

## Purpose
Compute per-container and per-work-package NPI KPIs (elapsed working days vs
targets → Green/Yellow/Red) for every OPEN SMT PCBA Work Container in **both
Singapore and Trutnov**, write `kpi_cache.json`, and upload it as a Confluence
attachment. A Tampermonkey userscript ("JIRA KPI Overlay") downloads that cache
and draws the coloured pills on the JIRA Kanban cards.

## Category
General (KPI reporting)

## Trigger
Daily 09:30 on the company laptop (Windows Task Scheduler), `--live`.

## Systems Involved
- [x] JIRA — read — open Work Containers + their child Work Packages
- [ ] M3 ERP — no
- [ ] EDM Oracle — no
- [x] Confluence — write — uploads `kpi_cache.json` attachment to page 572629046
- [x] Other — the Tampermonkey userscript consumes the attachment (pure renderer,
      unchanged; its `@match` already covers the Trutnov board)

## Input
No input — a scheduled full sweep. Scope is fixed by JQL:
`issuetype = "Work Container" AND "Product Type" = "SMT PCBA" AND "NPI Location"
in ("Singapore","Trutnov") AND resolution is EMPTY`.

## Logic
1. Fetch all open SMT PCBA containers (Singapore + Trutnov) from JIRA.
2. For each container, fetch child Work Packages via the `Project Children`
   relation JQL.
3. Pick the target set + holiday calendar by **NPI Location** (customfield_13906):
   - Singapore: Overall 24, Logistics 4, Documentation 4
   - Trutnov:   Overall 21, Logistics 1, Documentation 1
   - Material 15, PCB 15, Routing/PE/TE 5, SMT Build 5 (same both sites)
4. Compute container elapsed working days (NPI start → today), subtracting parked
   spans (multi park/unpark cycles; currently-parked freezes elapsed).
5. Compute per-WP elapsed vs each WP's target and colour it.
6. Write `outputs/kpi_cache.json`; in `--live`, upload it to Confluence.

## Output
`kpi_cache.json` attachment on Confluence page 572629046. Each container entry
carries `location`, `target`, `elapsed`, `color`, `wpKpis[]`; there is also a
flat `workPackageKpis[]` list keyed by `issueKey` for the userscript.

## Fields & Data Mapping

### JIRA Fields
| Field | Custom Field ID | Purpose |
|-------|----------------|---------|
| NPI Location | customfield_13906 | Selects target set + holiday calendar (Singapore/Trutnov) |
| Order Type | customfield_13905 | Cache metadata |
| Product Type | customfield_13904 | JQL filter (SMT PCBA) |
| Request Type | customfield_13903 | Cache metadata |
| Issue_parked_log | customfield_15800 | Parked spans subtracted from elapsed |
| PTxx Document | customfield_13907 | Cache metadata (`projectId`) |

## Targets (core/kpi_core.py TARGETS_V5)
| Bucket | Singapore | Trutnov |
|---|---|---|
| Overall (T_NPI) | 24 | 21 |
| Material / PCB | 15 / 15 | 15 / 15 |
| Routing / PE / TE TechnPrep | 5 | 5 |
| SMT Build | 5 | 5 |
| Logistics | 4 | **1** |
| Documentation | **4** | 1 |

Two corrections vs the legacy standalone `kpi_core.py`:
- Singapore Documentation was **1** in the legacy `TARGETS_V5` (the long-flagged
  bug — the legacy live overlay masked it by hardcoding 4 in its own WP_CONFIG).
  Restored to **4** and now sourced from `TARGETS_V5`.
- Trutnov Logistics was **4** in the legacy `TARGETS_V5`; confirmed target is
  **1** (user decision, this migration).

## Edge Cases
- Container with an unknown/blank NPI Location → falls back to the Singapore
  target set + calendar (never dropped).
- Container with no recognised WPs, or no active WP entry date → skipped.
- Currently-parked container → elapsed freezes at the last park's start.
- WP resolved but not Done/Acknowledged (Won't Do/Cancelled) → shown as a grey
  "skipped" pill, excluded from NPI-start anchoring.

## Mock Data
`mock_data/containers.json` + `mock_data/children/<WC_KEY>.json` — one Singapore
(USRE-1001) and one Trutnov (POSX-2002) container with identical WP dates, so the
per-location target difference is visible (Logistics: SG Green vs Trutnov Red).

## Acceptance Criteria
- [x] JQL includes both Singapore and Trutnov.
- [x] Targets/holidays chosen per container by NPI Location.
- [x] Singapore Documentation target = 4; Trutnov Logistics target = 1.
- [x] `python -m tasks.kpi_overlay.main --mock` runs and writes a cache with
      per-container `location`/`target` and per-WP pills.
- [ ] `--live` on the laptop uploads the attachment to page 572629046 and the
      Trutnov board shows pills (verify in browser).

## Provenance
Migrated from `C:\Users\tmoghanan\Documents\AI\LiveKPI_Overlay\live_kpi.py` +
`kpi_core.py`. Hardcoded JIRA/Confluence PATs were dropped — credentials now come
from `config/config.yaml` via `core/config_loader.py`.
