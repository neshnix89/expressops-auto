# Task: mo_trigger_comment

## Purpose
When prerequisite Work Packages are done and SMT Build hasn't started,
generate a formatted MO-trigger comment with all the data the planner needs
(item table, dates, E5 status, routing checks, delivery info), stage it on
Confluence for human review, and allow selective push to JIRA.

## IMPORTANT — All Discovery Is Complete
Every table name, column name, SQL query, JIRA field, and HTML structure in
this spec has been confirmed with real data on the company laptop. Do NOT
run discovery queries. Do NOT guess or invent table/field names. Do NOT add
Discovery sections. If something isn't specified here, ask — don't guess.

## Category
General

## Trigger
On-demand. Operator runs manually when ready to trigger MOs.

## Systems Involved
- [x] JIRA — read — WP statuses, WP assignees, container description, Order Type, reporter
- [x] M3 ERP (ODBC) — read — routing operations (MPDOPE), BOM packaging (MPDMAT+MITMAS_AP), item/prod status (MITMAS_AP+MPDHED)
- [ ] M3 ERP (Playwright) — Phase 2 only, not this build
- [ ] Confluence write — Phase 3 only, not this build
- [ ] JIRA write — Phase 3 only, not this build

---

## Scope: Build Phase 1 Only

Phase 1 covers:
- Readiness gate (JIRA WP status check)
- JIRA data assembly (item table, delivery info, assignees, pilot-run, Programme IC, FYI)
- M3 ODBC enrichment (routing, BOM packaging, partial E5)
- Comment assembly + console output + file save

Do NOT build Phase 2 (Playwright XECX450) or Phase 3 (Confluence + JIRA push).

---

## Files To Create

```
tasks/mo_trigger_comment/
├── TASK.md          ← This file (already provided)
├── main.py          ← Entry point: CLI args, orchestration, console output
├── logic.py         ← Pure business logic (no API calls): readiness gate,
│                      description parsing, comment assembly, pilot-run
│                      detection, Programme IC detection, date math
├── m3_checks.py     ← M3 ODBC queries: routing, BOM packaging, E5 partial
├── capture.py       ← Run on company laptop to save mock data
└── mock_data/       ← Empty dir (populated by capture.py)
```

Follow the `main.py`/`logic.py` split pattern from `tasks/to_status_check/`.
Follow the M3 ODBC query pattern from `tasks/bom_scanner/`.

---

## CLI Interface

```
python -m tasks.mo_trigger_comment.main --mock          # default, VPS testing
python -m tasks.mo_trigger_comment.main --live           # company laptop
python -m tasks.mo_trigger_comment.main --live --dry-run # fetch+check, no file output
```

Output: for each ready container, print the assembled comment to console
and save as `outputs/mo_trigger_{KEY}.txt`.

---

## Architecture Rules

1. `--mock` default. Mock reads from `mock_data/`. Live hits JIRA + M3.
2. Credentials from `core/config_loader.py` → `config.yaml`. Never hardcode.
3. Separate I/O (main.py) from pure logic (logic.py).
4. Use `core/jira_client.py` for JIRA. Use `core/m3.py` for ODBC (or extend it).
5. Use `core/logger.py` for logging.
6. Errors: log and continue, never crash silently.
7. Type hints on signatures. Docstrings on public functions.

---

## Input

JQL for open containers (reuse from live_kpi.py):
```
issuetype = "Work Container"
  AND "Product Type" = "SMT PCBA"
  AND "NPI Location" = "Singapore"
  AND resolution is EMPTY
  ORDER BY created ASC
```

Child WP fetch:
```
issue in relation("{wc_key}", "Project Children", level1)
```

WP fields needed: `key, summary, status, resolution, resolutiondate, assignee`

---

## Logic

### Step 1 — Readiness Gate

A container is **ready** when ALL of:

| WP Name (case-insensitive) | Required State |
|----------------------------|---------------|
| Material | resolution in {Done, Acknowledged, Won't Do} |
| PCB | resolution in {Done, Acknowledged, Won't Do} |
| Routing - TechnPrep | resolution in {Done, Acknowledged, Won't Do} |
| PE - TechnPrep | resolution in {Done, Acknowledged, Won't Do} |
| TE - TechnPrep | resolution in {Done, Acknowledged, Won't Do} |
| SMT Build | status is NOT "Done" AND NOT "In Progress" |

"Won't Do" counts as resolved on every prerequisite WP — planners use it
whenever a step is legitimately skipped (e.g. Programme IC skipping PE,
reused PCB skipping PCB, existing routing skipping Routing/TE).

### Step 2 — Extract JIRA Data

#### 2a. Item Table (from Description HTML)

Fetch container with `expand=renderedFields`. Parse with BeautifulSoup.

Find `<div class="panelHeader">` containing text "NPI Built Type".
From that panel's parent, find the next `<table class='confluenceTable'>`.
Skip `<th>` header row. Each data `<tr>`:
- Col 0 `<td>`: Part number inside `<a>` tag, strip leading `#`
- Col 1 `<td>`: Description text
- Col 2 `<td>`: Request Qty number, append "pcs"

Confirmed HTML:
```html
<div class="panelHeader"><b>NPI Built Type &amp; Quantities</b></div>
...
<table class='confluenceTable'>
  <tr>
    <th>Part Number PCBA</th>
    <th>Type Description</th>
    <th>Request Qty</th>
    <th>FOQ Qty</th>
    <th>Yearly Forecast</th>
  </tr>
  <tr>
    <td>#<a href="...?B_MMITNO=70195582...">70195582</a></td>
    <td>PCBA S R200-PRT MB V1.91</td>
    <td>72</td>
    ...
  </tr>
</table>
```

Add computed columns:
- **SMT Line**: hardcoded "Line 5"
- **MO start**: next working day after today (skip weekends + SG holidays)
- **MO end**: MO start + 4 working days
- **Date format**: "21st April 2025" (ordinal day + full month + year)

#### 2b. Delivery Info

Inside "Purpose of the NPI" panel, find `<td>` with bold "Usage of Samples",
take next sibling `<td>` text verbatim.

Confirmed HTML:
```html
<div class="panelHeader"><b>Purpose of the NPI</b></div>
...
<td><b>Usage of Samples</b></td>
<td>Samples used for FG prototype build</td>
```

#### 2c. WP Assignees

| WP Name | Used For | Fallback |
|---------|----------|----------|
| PE - TechnPrep | Program creation trigger | "[UNASSIGNED]" + warning |
| TE - TechnPrep | AOI/Test line attribution | "[UNASSIGNED]" + warning |
| QM P+L | MOI check (pilot run only) | "Chern JR Daniel" |

Assignee = `fields.assignee.displayName`.
Also collect ALL WP assignees for FYI list.

#### 2d. Pilot Run Detection (two-signal check)

| Signal | Source | Pilot Value |
|--------|--------|-------------|
| Order Type | `customfield_13905` on container | `"PR – Pilot Run"` (em-dash) |
| QM P+L WP | child WP named "QM P+L" exists | WP present |

Both true → Pilot Run. Both false → not. Mismatch → warn + treat as pilot.

JIRA `labels` is NOT relevant (confirmed empty on all pilot containers).

#### 2e. Programme IC Detection

Both must be true:
- Container summary OR description contains "ICUC"
- PE - TechnPrep resolution = "Won't Do"

Effect: skip "PE Please reuse buyoff Board" line.

#### 2f. Addressee

Always "Ng Ker Cheng Hazel" (from config).

#### 2g. FYI List

Default (always):
- Moghanan Thinesh Neo Wei Siang
- Teo Geok Hui
- Sawabi Siti Aslinda
- Jainutdeen Jahabar
- Ng Ker Cheng Hazel

Additional (deduplicated by displayName):
- Container reporter (`fields.reporter.displayName`)
- All WP assignees from every child WP

#### 2h. IMR Number

Deferred (separate task). Output: `IMR: [pending]`

### Step 3 — M3 ODBC Enrichment (per article number)

#### 3a. Partial E5 Status

Item status:
```sql
SELECT MMITNO, MMSTAT FROM PFODS.MITMAS_AP WHERE MMITNO = ?
```

Prod status:
```sql
SELECT PHPRNO, PHSTAT FROM PFODS.MPDHED
WHERE PHPRNO = ? AND PHSTRT = 'STD' AND PHFACI = 'MF1'
```

Valid: MMSTAT and PHSTAT in ('20', '30', '40').
Both pass → "E5: Item sts ✓, Prod sts ✓ — R&D/Production release: check manually"
Either fail → "⚠ E5: CHECK REQUIRED — Item sts: {val}, Prod sts: {val}"

#### 3b. Routing Operations

```sql
SELECT POOPNO, POOPDS, PODOID FROM PFODS.MPDOPE
WHERE POPRNO = ? AND POSTRT = 'STD' AND POFACI = 'MF1'
ORDER BY POOPNO
```

Confirmed columns: POOPNO=op number (Decimal), POOPDS=description, PODOID=doc number.

**Check 1 — Breaking Array:**
POOPDS contains "BREAKING-ARRAY" (case-insensitive).
Doc validation: PODOID matches `^77-[A-Za-z0-9]{3,5}$`, not all-zeros.
- Valid → "Breaking already included in routing. Doc: {PODOID}"
- Invalid doc → "⚠ Breaking in routing but doc invalid: {PODOID}"
- Not found → "⚠ No BREAKING-ARRAY in routing"

**Check 2 — AOI:**
POOPDS starts with "AOI". Confirmed at ops 290, 561.

**Check 3 — Test:**
POOPDS contains "TEST" and "ARRAY". Confirmed: "TEST - ARRAY" at op 800.

Output:
- Both → "{TE_assignee} AOI and Test required"
- Only AOI → "{TE_assignee} AOI required"
- Only Test → "{TE_assignee} Test required"
- Neither → "{TE_assignee} ⚠ No AOI or Test in routing"

**Check 4 — Packing:** POOPDS contains "PACKING". Log-only, no output line.

#### 3c. BOM Packaging Material

```sql
SELECT PMMTNO FROM PFODS.MPDMAT
WHERE PMPRNO = ? AND PMSTRT = 'STD' AND PMFACI = 'MF1'
  AND TRIM(PMDWPO) = '5000'
```
CRITICAL: PMDWPO is a STRING column. Must use TRIM() + string comparison.

Then:
```sql
SELECT MMITNO, MMITDS FROM PFODS.MITMAS_AP WHERE MMITNO = ?
```

- Exists + starts with "PM" → "Packaging Material ({PMMTNO}) already in BOM"
- No Dwgpos 5000 → "⚠ No packaging material (Dwgpos 5000) in BOM"
- Not PM → "⚠ Dwgpos 5000 component {PMMTNO} not PM: {MMITDS}"

### Step 4 — Comment Assembly

```
Hi {addressee},

Please proceed for {order_type_label} MO planning of PCBAs shown below.

| Item Number | Description | Qty | SMT Line | MO start | MO end |
|-------------|-------------|-----|----------|----------|--------|
| {part_no}   | {desc}      | {qty}pcs | Line 5 | {start} | {end} |

Please trigger @{pe_assignee} for the program creation.

[PILOT RUN ONLY:]
Please include in MO F6 text: Please Trigger {qm_assignee} before packaging for MOI Check.

E5: {e5_status}

Depaneling Required
{breaking_status}
{packaging_material_status}
Delivery: {delivery_info}
IMR: [pending]

[IF NOT PROGRAMME IC:]
PE: Please reuse buyoff Board

{te_assignee} {aoi_test_status}

FYI: {fyi_list}
```

Order type label mapping:
- "PR – Pilot Run" → "Pilot Run"
- "DMR - Direct manufacturing release" → "DMR"
- "QS – Qualification sample" → "Qualification Sample"
- "DS – Development sample" → "Development Sample"
- Other → raw value

---

## Config (add to config/config.example.yaml)

```yaml
mo_trigger_comment:
  addressee: "Ng Ker Cheng Hazel"
  qm_default_assignee: "Chern JR Daniel"
  smt_line: "Line 5"
  mo_duration_days: 4
  default_fyi:
    - "Moghanan Thinesh Neo Wei Siang"
    - "Teo Geok Hui"
    - "Sawabi Siti Aslinda"
    - "Jainutdeen Jahabar"
    - "Ng Ker Cheng Hazel"
  duplicate_marker: "#Ref: MO-Trigger#"
```

---

## capture.py Requirements

Create a capture script (pattern: `tasks/to_status_check/capture.py`) that
runs on company laptop with `--live` to save mock data:

1. JIRA search results (open SG SMT PCBA containers) → `mock_data/search_results.json`
2. 5 individual containers with `expand=renderedFields` + comments → `mock_data/issue_{KEY}.json`
3. Child WPs per container → `mock_data/wps_{KEY}.json`
4. For article numbers found in descriptions:
   - M3 MPDOPE routing → `mock_data/routing_{article}.json`
   - M3 MPDMAT Dwgpos 5000 → `mock_data/bom_pkg_{article}.json`
   - M3 MITMAS_AP → `mock_data/item_{article}.json`
   - M3 MPDHED → `mock_data/prodstatus_{article}.json`

---

## Working Days

For MO start/end date calculation, need a working-day function that skips
weekends (Sat/Sun) and Singapore public holidays. Check if `kpi_core.py`
HOLIDAYS dict is importable. If not, implement standalone with SG holidays
for 2025-2026.

---

## JIRA Fields Reference

| Field | Source | Purpose |
|-------|--------|---------|
| WP summary | `fields.summary` | WP name matching |
| WP status | `fields.status.name` | Readiness gate |
| WP resolution | `fields.resolution.name` | Done/Won't Do |
| WP assignee | `fields.assignee.displayName` | PE/TE/QM person + FYI |
| Description | `renderedFields.description` | Item table, delivery, ICUC |
| Summary | `fields.summary` | ICUC detection |
| Reporter | `fields.reporter.displayName` | FYI list |
| Order Type | `customfield_13905.value` | Pilot run + header |
| Product Type | `customfield_13904` | Filter |
| NPI Location | `customfield_13906` | Filter |

## M3 Tables Reference (all confirmed)

| Table | Columns Used | Purpose |
|-------|-------------|---------|
| MPDOPE | POPRNO, POSTRT, POFACI, POOPNO, POOPDS, PODOID | Routing ops |
| MPDMAT | PMPRNO, PMMTNO, PMDWPO (STRING!), PMSTRT, PMFACI | BOM packaging |
| MITMAS_AP | MMITNO, MMSTAT, MMITDS | Item status + desc |
| MPDHED | PHPRNO, PHSTRT, PHFACI, PHSTAT | Prod status |

---

## Edge Cases
1. No "NPI Built Type" section → skip container + warning
2. Multiple articles → all in same table, each gets M3 checks
3. PE/TE WP unassigned → "[UNASSIGNED]" + warning
4. QM P+L unassigned → default "Chern JR Daniel"
5. Pilot run mismatch (Order Type vs QM P+L) → warn, treat as pilot
6. Programme IC (ICUC + PE Won't Do) → skip buyoff line
7. E5 outside 20-40 → warning in comment
8. Breaking doc generic (77-0000) → warning
9. No routing ops → warning
10. Article not found in M3 → warning
11. Duplicate marker in comments → skip + warn
12. HTML structure changed → skip + error log

## Acceptance Criteria
- [ ] Readiness gate identifies correct containers
- [ ] Comment matches reference format
- [ ] Pilot run: Order Type + QM P+L checked, mismatch flagged
- [ ] Programme IC: ICUC + PE Won't Do → no buyoff line
- [ ] Item table parsed from description HTML
- [ ] Delivery info parsed from description HTML
- [ ] E5 partial: item/prod status validated
- [ ] Routing: BREAKING-ARRAY (doc validated), AOI, TEST-ARRAY detected
- [ ] BOM packaging: Dwgpos 5000 + PM prefix identified
- [ ] FYI: defaults + reporter + WP assignees, deduplicated
- [ ] Duplicate guard prevents re-triggering
