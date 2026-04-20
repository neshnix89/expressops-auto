# Claude Code Handoff: tasks/bom_scanner

## Goal

Build `tasks/bom_scanner/` — a module that scans Work Containers from two sources
(JIRA open containers + Confluence MR Status page), extracts article numbers from
each container's DESCRIPTION field, looks up the BOM components in M3 via ODBC,
checks every component's PLC status, flags any component where PLC ≠ 310, and
posts a JIRA comment on flagged containers mentioning the requestor. Results publish
to a Confluence page. Same `--mock`/`--live` split as every other task.

---

## Files to Read First

Read these in order before writing any code:

1. `CLAUDE.md` — project rules, architecture, coding standards
2. `tasks/bom_scanner/TASK.md` — original spec (NOTE: references to "PDS002"
   are wrong — the actual program is PDS001, and the data path is ODBC not
   Playwright, so the program name is irrelevant to implementation)
3. `tasks/to_status_check/main.py` — reuse ACTIVE_CONTAINERS_JQL verbatim;
   mirror the run() orchestration structure (Phase A / Phase B / Phase C)
4. `tasks/to_status_check/publish.py` — pattern for rendering rows → Confluence
   storage HTML and pushing via core.confluence; mirror the code-macro footer
5. `core/confluence.py` — get_page_html(), get_page(), update_page()
6. `core/jira_client.py` — add_comment(), get_issue(), search(), search_all()
7. `core/config_loader.py` — how config.yaml is loaded (use this, don't parse
   YAML directly)
8. `core/logger.py` — standardized logging

Do NOT read or use `clients/m3_h5_client.py` — this task uses ODBC, not Playwright.

---

## Resolved Discovery Answers

### 1. PLC Data Path
ODBC to PFODS. No Playwright, no REST API, no browser automation.
The PLC value lives in `MITMAS_AP.MMCFI3` (Custom Field Information 3).

### 2. PLC Column
`MMCFI3` on table `PFODS.MITMAS_AP`.
Values are strings: '310', '200', '602', 'INT', 'NEW', ' ' (blank), etc.
Not always numeric — treat as string comparison.

### 3. Flag Rule
Flag any BOM component where `TRIM(MMCFI3) != '310'`.
PLC 310 = "Without limitation" — full sales release, no restrictions.
Everything else (development, locked, phase-out, terminated, non-standard codes
like 'INT' or 'NEW', or blank) is a problem.

### 4. BOM Component Lookup
Table: `PFODS.MPDMAT` (3.1 million rows, confirmed populated)
Key columns:
- `PMPRNO` = parent/product number (the article number from the container)
- `PMMTNO` = component/material number
- `PMSTRT` = structure type (filter to 'STD' only)
- `PMFACI` = facility (filter to 'MF1')
- `PMDWPO` = reference designator (for display only)
- `PMCNQT` = component quantity

The same component appears at multiple BOM positions (different PMDWPO).
Use DISTINCT on PMMTNO to avoid checking the same part twice.

Header table: `PFODS.MPDHED`
- `PHPRNO` = product number
- `PHSTRT` = structure type
- Can verify the product exists in PDS before querying materials.

### 5. Core SQL Query
```sql
SELECT DISTINCT m.PMMTNO, i.MMCFI3, i.MMSTAT, i.MMITDS
FROM PFODS.MPDMAT m
JOIN PFODS.MITMAS_AP i ON i.MMITNO = m.PMMTNO
WHERE m.PMPRNO = ?
  AND m.PMSTRT = 'STD'
  AND m.PMFACI = 'MF1'
  AND TRIM(i.MMCFI3) != '310'
```
If this returns rows → container is flagged (has non-310 components).
If empty → BOM is clean.

### 6. Article Number Extraction — USE DESCRIPTION, NOT SUMMARY

**IMPORTANT: Extract article numbers from the DESCRIPTION field, not Summary.**
The Description field is a JIRA wiki-markup text body containing a structured NPI
form. Article numbers appear throughout it.

Coverage with proper SMT PCBA + Singapore filtering:
- Summary only: 55%
- **Description: 78%**
- **Either: 82%**

A single container can have MULTIPLE article numbers in its description. The scanner
should extract ALL of them and check each one's BOM.

Extraction function — try multiple patterns:
```python
import re

def extract_article_numbers(text: str) -> list[str]:
    """Extract all article numbers from description text."""
    if not text:
        return []
    found = []
    # Pattern 1: #70203371 (explicit hash prefix, 6-8 digits)
    for m in re.finditer(r'#(\d{6,8})', text):
        found.append(m.group(1))
    # Pattern 2: Y70184012 (Y prefix in product codes)
    for m in re.finditer(r'Y(\d{7,8})', text):
        found.append(m.group(1))
    # Pattern 3: standalone 7-8 digit number starting with 70
    for m in re.finditer(r'\b(70\d{5,6})\b', text):
        found.append(m.group(1))
    # Pattern 4: PCB# or PCBA# followed by number
    for m in re.finditer(r'(?:PCB|PCBA)\s*#?\s*(\d{6,8})', text, re.IGNORECASE):
        found.append(m.group(1))
    return list(set(found))  # dedupe
```

When fetching issues from JIRA, request the description field:
`fields=key,summary,description,reporter`

Containers with no extractable article numbers (~18%) are typically early-stage
development with no product structure in M3 yet. Skipping them is correct behavior.

### 7. Requestor Field
Use the built-in `reporter` field on the JIRA issue.
There is no custom "Requestor" field.
Access: `issue["fields"]["reporter"]["name"]`

Note: The description field also contains a "Name of Requestor" line in the NPI
form, but `reporter` is the reliable structured field — use that.

For closed containers from Confluence, fetch the issue via get_issue() to get reporter.

### 8. JIRA Mention Syntax
`[~username]` where username = `reporter["name"]`
Example: `[~geschaefer]`
Confirmed from live comment: `[~sheng] [~ykor] [~Alexia] [~rreyes]`

### 9. Comment Body Template
```
[~{reporter_name}] BOM PLC Check — the following components in article
{article_number} have a PLC status other than 310:

{component_table}

Please update the PLC status to {target_status} before proceeding with MR.

(Automated by BOM Scanner)
```
Where component_table lists: Component Number | Current PLC | Description
If a container has multiple article numbers, group flagged components by article.

### 10. Confluence Page 560866215 — HTML Structure
Three tables on the page:
- **Table 1: "MR Week Schedule"** (purple headers, `rgb(142,68,173)`)
  - Column 0: MR Week
  - Column 1: Container Numbers (as `<a>` links)
  - 16 columns total including MR Status, Remarks
- **Table 2: "Active MR"** (blue headers, `rgb(74,144,217)`)
  - Column 0: Container Numbers (as `<a>` links)
  - 15 columns total
- **Table 3: "COMPLETED MR"** — skip this table entirely

Container keys are inside `<a href="https://pfjira.pepperl-fuchs.com/browse/KEY">KEY</a>`

BeautifulSoup extraction:
```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(html, "html.parser")
tables = soup.find_all("table")
# tables[0] = MR Week Schedule
# tables[1] = Active MR
# tables[2] = COMPLETED MR (skip)

container_keys = set()
for table in tables[:2]:  # Only first two tables
    for a_tag in table.find_all("a", href=True):
        href = a_tag["href"]
        if "/browse/" in href:
            key = href.split("/browse/")[-1]
            container_keys.add(key)
```

### 11. Target Status CLI
`--target-status <code>` — no default value. Script refuses to run if not provided.
Operator typically passes `310` but could pass other codes.

### 12. New Confluence Page
Page ID: `572180443`
Already added to config.yaml. Also add to `config/config.example.yaml` (tracked):
```yaml
pages:
  bom_scanner: 572180443
```

### 13. ODBC Connection
```python
import pyodbc
conn = pyodbc.connect("DSN=ODSSG")
```
- No timeout parameter
- Oracle SQL syntax: use `ROWNUM`, not `TOP N`
- String columns may be padded — use `TRIM()` in comparisons
- Use positional `?` parameters for queries

### 14. JQL for JIRA Source
Reuse the exact ACTIVE_CONTAINERS_JQL from `tasks/to_status_check/main.py`.
This filters to SMT PCBA + Singapore + open status. Total ~557 containers.
Do NOT use the broad "Order Type is not EMPTY" JQL — that pulls 2,298 including
Final Goods and non-Singapore containers.

---

## Files to Create

### tasks/bom_scanner/main.py
Entry point. CLI args: `--mock`/`--live` (default: mock), `--target-status <code>`
(required), `--source jira|confluence|both` (default: both).

Orchestration:
1. **Phase A — Gather containers**
   - JIRA source: reuse ACTIVE_CONTAINERS_JQL from to_status_check/main.py.
     Fetch fields: key, summary, description, reporter.
   - Confluence source: get_page_html(config pages.mr_status_report),
     parse with BeautifulSoup, extract keys from tables 1+2 (skip COMPLETED).
     Then fetch each container via get_issue() to get description + reporter.
   - Union both sources, dedupe by key.
2. **Phase B — Extract article numbers + BOM PLC check**
   - For each container: extract_article_numbers() from description field.
   - For each article number: ODBC query MPDMAT+MITMAS_AP for flagged components.
   - A container can have multiple article numbers — check all.
3. **Phase C — Act**
   - For flagged containers: add_comment() with [~reporter] mention.
   - Check for existing "(Automated by BOM Scanner)" comment before posting
     to avoid duplicates on re-run.
   - Publish results to Confluence page 572180443.
   - Console summary table.

### tasks/bom_scanner/logic.py
Pure functions (no I/O):
- `extract_article_numbers(text: str) -> list[str]` — multi-pattern regex
- `dedupe_containers(jira_keys: list, confluence_keys: set) -> list[dict]`
- `build_comment_body(reporter_name, article_number, flagged_components, target_status) -> str`
- `should_flag(components: list[dict]) -> bool` — any component PLC != 310
- `build_confluence_rows(results: list[dict]) -> list[dict]`

### tasks/bom_scanner/publish.py
Mirror tasks/to_status_check/publish.py pattern.
Renders results table → Confluence storage HTML → update_page().
Columns: Container | Source | Article # | Flagged Components (count) |
         Component Details | Reporter | Action Taken
Include code-macro footer with timestamp and --target-status used.

### tasks/bom_scanner/capture.py
Run on company laptop to save mock data:
- JIRA search results (active containers with description field)
- 5-10 sample container issue JSONs (with reporter + description)
- Confluence page 560866215 storage HTML
- ODBC MPDMAT results for 2-3 known article numbers
- ODBC MITMAS_AP PLC for those components

### tasks/bom_scanner/mock_data/
- `jira_search.json`
- `issue_USRE-1039.json` (has #70204501 in description)
- `issue_USRE-551.json` (has #70198800 in description)
- `issue_SILED2-3292.json` (multiple article numbers in description)
- `issue_NPIOTHER-201.json` (from Confluence Active MR table)
- `confluence_560866215.html`
- `m3_bom_70203371.json` (MPDMAT+MITMAS join results — all PLC=310, clean)
- `m3_bom_70198800.json` (should contain some non-310 for testing)

### config/config.example.yaml
Add under `pages:`:
```yaml
  bom_scanner: 572180443
```

---

## Edge Cases to Handle

1. Container in both JIRA and Confluence → dedupe by key, comment once, note
   both sources in output.
2. No article number extractable from description → skip, log warning, show in
   Confluence output as "No article #". (~18% of containers — correct behavior
   for early-stage containers.)
3. Multiple article numbers in one container → check BOM for each, aggregate
   all flagged components into one comment.
4. Article number not found in MPDMAT (no BOM exists) → skip, log warning,
   show as "No BOM in M3".
5. Article number found but no MPDHED with STRT='STD' → same as above.
6. Reporter is None or inactive → skip comment, log warning, flag for manual
   follow-up in Confluence output.
7. `--target-status` not supplied → refuse to run, print usage.
8. Duplicate comments on re-run → check for "(Automated by BOM Scanner)"
   marker in existing comments; if found, skip.
9. MMCFI3 contains non-numeric values ('INT', 'NEW', blank) → all flagged
   correctly by the `TRIM(MMCFI3) != '310'` rule.
10. Confluence page layout changes → BeautifulSoup uses the stable
    `<a href="/browse/">` pattern; if zero containers extracted, log error
    and abort rather than silently proceeding.

---

## Acceptance Test Steps

1. Run `python -m tasks.bom_scanner.main --mock` with no `--target-status`
   → verify it refuses to run.
2. Run `python -m tasks.bom_scanner.main --mock --target-status 310`
   → verify it produces expected flagged rows from mock data with no network.
3. Verify comment bodies contain correct `[~reporter]` mentions, component
   lists grouped by article number, and the target status.
4. Run `python -m tasks.bom_scanner.main --live --target-status 310 --source jira`
   → test on a small batch; verify JIRA comments and Confluence page update.
5. Re-run the same command → verify no duplicate comments posted.
6. Check Confluence page 572180443 has results table with timestamp.

---

## Do NOT

- Do NOT use Playwright or clients/m3_h5_client.py — this is pure ODBC
- Do NOT use customfield_13502 or customfield_15805 — they are empty on all containers
- Do NOT extract article numbers from Summary — use Description field
- Do NOT guess table names or column names — all are confirmed above
- Do NOT write to JIRA or Confluence in --mock mode
- Do NOT hardcode credentials — use core/config_loader.py
- Do NOT process the COMPLETED MR table from Confluence (table index 2)
- Do NOT use "PDS002" anywhere — the correct program is PDS001, and we don't
  interact with the program at all (pure ODBC)
