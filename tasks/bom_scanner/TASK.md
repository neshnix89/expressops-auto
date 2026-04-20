# Task: bom_scanner

## Purpose
Scan Work Containers from two sources (open in JIRA + closed-but-ready-for-MR on a Confluence page) and flag any whose BOM has an M3 PDS002 PLC status other than **310**. For each flagged container, add a JIRA comment mentioning the requestor and ask them to change the PLC status. The operator chooses the target status per run.

## Category
General

## Trigger
On-demand (CLI); can be scheduled later if the flow proves stable.

## Systems Involved
- [x] JIRA — read/write — pull open containers via JQL; pull closed containers by key to find requestor; add comments with @mention
- [x] M3 ERP — read — PDS002 program / BOM tables to read PLC status per container
- [ ] EDM Oracle — not used
- [x] Confluence — read/write — read MR Status Report page (560866215) to enumerate closed containers; write scanner results to a **new** Confluence page
- [ ] Other: none

## Input
- CLI: `--mode live|mock`, `--target-status <code>` (PLC value to request), optional `--source jira|confluence|both` (default: both)
- Config: `config.yaml` → `pages.mr_status_report` (existing, 560866215), `pages.bom_scanner` (new, to be assigned)

## Logic
1. **Gather containers**
   - JIRA source: reuse the ACTIVE_CONTAINERS_JQL pattern from `tasks/to_status_check/main.py` (ITPL template relations → SMT PCBA → Singapore → open statuses).
   - Confluence source: `get_page_html(560866215)`, parse with BeautifulSoup, extract container keys from the MR-ready table.
   - Union both sources, dedupe by key.
2. **Fetch requestor per container**
   - For each key: `jira.get_issue(key)` and read the requestor field (see Discovery — Reporter vs. custom field).
3. **BOM lookup in M3 PDS002**
   - For each container's associated item/BOM, read the PLC status from M3.
   - The exact data path is a Discovery item (ODBC table candidate vs. Playwright against PDS002 MNE panel).
4. **Flag**
   - Flagged = PLC status ≠ `310`.
5. **Act**
   - For every flagged container (both sources), post a JIRA comment on the container with the requestor `[~username]` mention and a standard body: "Current PLC status is `<current>`; please change to `<target-status>`."
   - Target status comes from `--target-status` (no default — operator must pass it).
6. **Publish**
   - Render results table to the new Confluence page (page ID TBD). Columns: Container, Source, Status, Requestor, Current PLC, Action Taken.

## Output
- Console: summary table and counts (total scanned, flagged, commented, errors).
- Confluence: new page with the flagged list + timestamp + the `--target-status` used.
- JIRA: one comment per flagged container, with @mention and requested status change.

## Fields & Data Mapping

### JIRA Fields
| Field | Custom Field ID | Purpose |
|-------|----------------|---------|
| Summary | (built-in) | Container title |
| Status | (built-in) | Container workflow status |
| Reporter | (built-in `reporter`) | Candidate for "requestor" — to be confirmed in Discovery |
| Requestor (custom) | customfield_XXXXX | Alternate candidate; confirm via live JIRA field API |
| Product Type | customfield_13904 | Confirm SMT PCBA scoping |
| NPI Location | customfield_13906 | Confirm Singapore scoping |
| Order Type | customfield_13905 | Used by existing JQL pattern |

### M3 Tables
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| MITMAS_AP | MMITNO, MMSTAT | Likely item-master status; may NOT be the PLC field — verify |
| (Discovery) | (Discovery) | PDS002-specific table that actually holds the "310" PLC value — unknown until investigated |

## Discovery Notes
1. **PDS002 data path** — is PLC status readable via PFODS ODBC, or is PDS002 MNE-only like XDRX800 (would require Playwright, same pattern as `clients/m3_h5_client.py`)? Check on company laptop: open PDS002, view-source, look for a panel-level ODBC-visible field or a generic.do XHR like XDRX800.
2. **PLC column name and table** — exact M3 column that holds the `310` value. Candidates to rule out: `MMSTAT` on MITMAS, `MBSTAT` on MBMHED, a PDS-specific field. Do not guess.
3. **"310" semantics** — is `310` the only acceptable value, or is any code in a range OK? Is "not 310" the flag rule, or is it "< 310" / "in a specific list"? Confirm with the process owner.
4. **Requestor JIRA field** — Reporter (built-in) or a custom field like `customfield_xxxxx`? Verify against the live JIRA field API against a sample Work Container. For closed-via-Confluence containers, confirm the same field is still populated after closure.
5. **JIRA mention syntax** — on-prem JIRA comment bodies use `[~username]`. Confirm the username key (`name` vs `key` vs email vs domain-account) by looking at how existing automations mention users.
6. **Target status selection mechanism** — settled on CLI `--target-status <code>` per run (no default). Confirm this is what the operator wants, or whether it should be a per-row choice read from a Confluence column.
7. **Confluence page 560866215 scraping** — exact HTML structure of the MR Status Report table: container-key column index, table class/id, whether there's a "ready for MR" filter to apply. Save a sample of the rendered storage HTML and document the selectors.
8. **BOM → item linkage** — how do we go from a container to the BOM item number in M3? Via an existing JIRA field (e.g., `customfield_13502` = M3 Article Number, per CLAUDE.md), or via a lookup from the container through Work Packages?
9. **New Confluence page** — create the page manually first, add its ID to `pages.bom_scanner` in both `config/config.yaml` (local) and `config/config.example.yaml` (tracked).

## Edge Cases
- Container exists in both sources (JIRA open + Confluence MR-ready) → dedupe by key, comment once, note both sources in the output row.
- No BOM found in M3 for a container → log warning, mark row as "no BOM", do not comment.
- Container has no requestor / requestor is inactive → log warning, publish to Confluence without a comment, flag for manual follow-up.
- `--target-status` not supplied → refuse to run (safer than defaulting to a value the operator didn't pick).
- Rate limiting / duplicate comments on re-run → consider a marker comment or a "last scanned" timestamp on the container to avoid spamming on repeat runs.
- Confluence page 560866215 layout changes → BeautifulSoup parsing should fail loudly, not silently return empty.

## Mock Data Needed
- [ ] JIRA search result for ACTIVE_CONTAINERS_JQL (same pattern as `to_status_check`).
- [ ] Per-container JIRA issue JSONs with requestor field populated (open + closed samples).
- [ ] Confluence page 560866215 storage HTML snapshot.
- [ ] M3 BOM response for a known container — format depends on Discovery #1 (ODBC rows or XML from a generic.do capture).

## Acceptance Criteria
- [ ] Running `--mock` produces the expected flagged rows from saved mock data with no network access.
- [ ] Running `--live --target-status 310` on a known flagged container posts exactly one JIRA comment with a correct `[~requestor]` mention and the target status in the body.
- [ ] Closed containers pulled from the Confluence MR page get their requestor resolved from JIRA and receive the same comment.
- [ ] The new Confluence page is updated with the scan results; counts in the summary match the number of comments posted.
- [ ] Re-running on the same day does not double-post comments on already-processed containers (guardrail — see Edge Cases).
