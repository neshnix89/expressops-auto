# Legacy Reference

Domain knowledge extracted from the pre-framework scripts in
`C:\Users\Administrator\Documents\PY dump\` so the new framework does not have
to re-discover it. Source files:

- `Pilot_DMR_Report.py` — MR status report → Confluence 560866215
- `expressops_kpi.py` + `live_kpi.py` — KPI pipeline (daily cache + weekly report)
- `jira_npi_standup_daily_with_done (4).py` — NPI daily standup
- `JIRA Kanban Meeting Assistant v2.8 (Auto-Post & Extract).py` — Tampermonkey userscript
- `comment extractor.py` — ad-hoc comment export
- `Open containers during set date.py`, `Jira_Containers_Created_Date.py.py` — date-range container reports
- `excel_to_jira_V4_FIXED.py` — Excel → JIRA importer
- `m3_schema_dump.py` — M3 schema crawler
- `import pyodbc.py` — M3 connect snippet
- `M3_PFODS_Schema_Catalog.xlsx` — output of the schema dump (binary, not read)

Values below are literal. If a script computes something indirectly (e.g.
imports custom field IDs from `kpi_core.py`, which is not in the dump), that is
flagged rather than guessed.

---

## JIRA — connection

- Base URL: `https://pfjira.pepperl-fuchs.com`
- Auth: PAT in `Authorization: Bearer <token>` header
- SSL: `verify=False` on all requests (self-signed cert)
- Token validation endpoint: `GET /rest/api/2/myself` (used by `live_kpi.py` to
  confirm PAT before running)

### API endpoints actually used

| Endpoint | Method | Purpose |
|---|---|---|
| `/rest/api/2/search` | GET / POST | JQL search (paginated via `startAt`) |
| `/rest/api/2/myself` | GET | Validate PAT |
| `/rest/api/2/issue/{key}` | GET | Issue details; `expand=changelog` seen |
| `/rest/api/2/issue/{key}/comment` | GET | Comments only (Pilot_DMR_Report) |

Common query params: `jql`, `fields` (comma list), `startAt`, `maxResults`
(default 50, max 100), `expand`.

## JIRA — projects & templates

- **ITPL** — "Issue Template" project. Every production query excludes it via
  `project != "Issue Template"`.
- Known in-scope template keys (hardcoded in `jira_npi_standup_daily_with_done`
  and `comment extractor.py`):
  `ITPL-769, ITPL-760, ITPL-756, ITPL-750, ITPL-746, ITPL-742, ITPL-1036, ITPL-1027`
- `EXPRESSOPS` is the project key the new framework standardizes on (per
  `CLAUDE.md`); legacy scripts do not filter by project key — they filter by
  Product Type + NPI Location.

## JIRA — issue types & statuses seen

Issue types: `Work Container`, `Work Package`, `Task`, `Deviation`, `Issue Template`.

Statuses: `Waiting`, `Backlog`, `In Progress` (also appears lowercased `In progress`),
`Done`, `Closed`. `resolution is EMPTY` is used as the open/closed discriminator.

No numeric transition IDs are used anywhere in the dump. Status changes are
observed via the issue changelog (status name transitions), never posted back.

## JIRA — custom fields

Only one literal custom field ID appears in the dump:

| ID | Used in | Notes |
|---|---|---|
| `customfield_13905` | `Pilot_DMR_Report.py` (as `TAG_FIELD`) | Also documented in CLAUDE.md as **OrderType** (values: "Pilot Run", "DMR Request") — same field, different labels |

Every other custom field is referenced by **JQL display name** (which JIRA
resolves via the field name) or imported from `kpi_core.py` (not in the dump)
under symbolic names. The display names seen:

- `"Product Type"` — used as SMT PCBA filter
- `"NPI Location"` — used as Singapore filter
- `"Order Type"`
- `"Request Type"`
- `"Issue_parked_log"` — multiline START/END log, parking/pause periods
- `"Project ID"`, `"Project Status"`, `"NPI Work Container Status"`, `"Aggregated Progress"`

**Action item for the new framework:** resolve these display names to their
`customfield_XXXXX` IDs on the company laptop (one GET against `/rest/api/2/field`
will do it) and record them in this file. `CLAUDE.md` already has guesses for
`13903`–`13906` and `15800` but they are not confirmed against the legacy code.

## JIRA — JQL queries (literal)

### Open Singapore containers (live_kpi.py)
```
issuetype = "Work Container" AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" AND resolution is EMPTY ORDER BY created ASC
```

### Singapore NPI history, any status (Pilot_DMR_Report, Jira_Containers_Created_Date)
```
project != "Issue Template" AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" ORDER BY created ASC
```

### NPI standup daily (jira_npi_standup_daily_with_done)
```
issue in relation("issue in relation('key in (ITPL-769, ITPL-760, ITPL-756, ITPL-750, ITPL-746, ITPL-742, ITPL-1036, ITPL-1027)', 'Project Children', Tasks, Deviations, level4)", "Project Children", 'Clone from Template', level4) AND project != 'Issue Template' AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" AND status in (Waiting, "In Progress", Backlog, Done) ORDER BY key ASC
```

### All statuses, same scope (comment extractor.py)
Same as above minus the trailing `status in (...)` filter.

### Parent → children relation (repeated pattern)
```
issue in relation('{parent_key}', 'Project Children', Tasks, Deviations, level4)
issue in relation("{wc_key}", "Project Children", level1)
```

The nested `relation()` form is how legacy scripts walk
`Templates → cloned containers → Work Packages` in a single query. This is the
canonical way to enumerate all live WPs for the Singapore SMT PCBA scope.

## JIRA — container ↔ work package model

Hierarchy: `Templates (ITPL)` → `Clone from Template` link → `Work Container`
→ `Project Children` link (Tasks / Deviations / level4) → `Work Packages`.

Standard WP names (from `kpi_core`, referenced by `live_kpi.py`):

`Material`, `PCB`, `Routing - TechnPrep`, `PE - TechnPrep`, `TE - TechnPrep`,
`SMT Build`, `QM P+L`, `Logistics`, `Documentation`.

DMR alternate: a single WP named `Direct Manufacturing Release`.

**Case-insensitive WP name matching is required** (inconsistent casing is noted
in both CLAUDE.md and the parsing code).

## JIRA — parking log parsing

Field: `Issue_parked_log` (referenced symbolically as `CF_PARKED_LOG`).

Format: multiline body with `Start:YYYY-MM-DD HH:MM:SS;End:YYYY-MM-DD HH:MM:SS;`
pairs. If the last pair has no `End`, the container is currently parked.

Extraction regex (`live_kpi.py`, multiline mode):
```
Start|End:\s*(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?
```

KPI effect: working days inside parked spans are subtracted from elapsed; if
currently parked, elapsed freezes at the last `Start`.

## KPI targets (kpi_core.TARGETS_V5)

Not in the dump but referenced. Recorded here because the framework will need
them when Phase B of `to_status_check` or a KPI task is built:

| Bucket | Days |
|---|---|
| T_NPI (container, Singapore) | 24 |
| T_Material | 15 |
| T_PCB | 15 |
| T_Routing_TechnPrep | 5 |
| T_PE_TechnPrep | 5 |
| T_TE_TechnPrep | 5 |
| T_SMT_Build | 5 |
| T_QM_P+L | 0 (folded into SMT window) |
| T_Logistics | 4 (Singapore) / 4 (Trutnov) |
| T_Documentation | 4 (Singapore) / 1 (Trutnov) — a comment in `live_kpi.py` flags `TARGETS_V5` as having `1` for both, i.e. a suspected bug |
| T_DMR | 24 |

## Regex patterns worth reusing

| Pattern | Matches | Script |
|---|---|---|
| `QD\s*-\s*\d+` (IGNORECASE) | PE report codes `QD-xxxxx` | Pilot_DMR_Report |
| `906\s*-\s*[A-Za-z0-9]+` (IGNORECASE) | TE report codes `906-xxxxx` | Pilot_DMR_Report |
| `PT[A-Z0-9]{2}-[A-Z0-9]{4,5}` (IGNORECASE) | PT numbers, e.g. `PTA1-B2C34` | Pilot_DMR_Report |
| `^\s*MR\s*Week\s*(\d+)\s*$` (IGNORECASE) | MR-week marker in remarks | Pilot_DMR_Report |
| `Start\|End:\s*(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?` | Parking log timestamps | live_kpi |
| `Please proceed for.*(Prototype\|Pilot\|Eval).*Run MO planning` (IGNORECASE) | MO-planning trigger comment | npi_standup |
| `\bMO\s+\d{10,}` (IGNORECASE) | MO number `MO 7003595135` | npi_standup |
| `\bMO\s*:\s*\d{10,}` (IGNORECASE) | MO number `MO: 7003595135` | npi_standup |
| `\bMO\s+no\s+\d{10,}` (IGNORECASE) | MO number `MO no 7003595062` | npi_standup |
| `MO is created.*?(\d{10,})` (IGNORECASE, DOTALL) | MO in full sentence | npi_standup |
| `MO\s+no\s+.*?\n\s*(\d{10,})` (IGNORECASE, DOTALL) | MO in table layout (number on next line) | npi_standup |

TO-number pattern for the new `to_status_check` task (`TO:\s*(\d+)`) does not
appear in the dump — it is a net-new regex.

## Confluence

- Base URL: `https://pfteamspace.pepperl-fuchs.com`
- Space key: `EUDEMHTM0021`
- Auth: PAT Bearer
- Page IDs seen:

| ID | Purpose | Script |
|---|---|---|
| 560866215 | Pilot / DMR MR status report | Pilot_DMR_Report |
| 560871424 | ExpressOPS KPI report (per CLAUDE.md; confirmed mention) | expressops_kpi |
| 572629046 | KPI cache (JSON attachment `kpi_cache.json`) | live_kpi |
| 572178383 | M3 schema catalog | m3_schema_dump |

### Confluence API endpoints used
- `GET  /rest/api/content/{id}?expand=body.storage,version` — read page
- `PUT  /rest/api/content/{id}` — update (version must be incremented; conflict retry seen)
- `GET  /rest/api/content/{id}/child/attachment?filename={name}` — find existing attachment
- `POST /rest/api/content/{id}/child/attachment` — create attachment
- `POST /rest/api/content/{id}/child/attachment/{attId}/data` — update attachment
- `GET  /download/attachments/{id}/{filename}` — download attachment body

### Storage-format markup
Status badges use `ac:structured-macro ac:name="status"` with params
`ac:name="colour"` in `{Green, Red, Yellow, Grey}` and `ac:name="title"` for the
label. `Pilot_DMR_Report` parses existing pages to recover the `title` parameter
before overwriting — this is how manual team edits (PIC feedback, Handover,
Remarks, MR Status) are preserved, and it is the pattern CLAUDE.md calls out as
critical.

## M3 (ODBC)

- DSN: `ODSSG`
- Schema: `PFODS`
- Connect: `pyodbc.connect("DSN=ODSSG")` — no timeout param
- All accessible objects are **synonyms in `PFODS`** pointing to underlying
  owners; resolve via `ALL_SYNONYMS`.

### Schema crawl queries (m3_schema_dump.py)
```sql
SELECT SYNONYM_NAME, TABLE_OWNER, TABLE_NAME
  FROM ALL_SYNONYMS
 WHERE OWNER = ?

SELECT s.SYNONYM_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.DATA_LENGTH,
       c.DATA_PRECISION, c.DATA_SCALE, c.NULLABLE, c.COLUMN_ID
  FROM ALL_SYNONYMS s
  JOIN ALL_TAB_COLUMNS c
    ON c.OWNER = s.TABLE_OWNER AND c.TABLE_NAME = s.TABLE_NAME
 WHERE s.OWNER = ?
 ORDER BY s.SYNONYM_NAME, c.COLUMN_ID

SELECT TABLE_NAME, COLUMN_NAME, COMMENTS
  FROM ALL_COL_COMMENTS
 WHERE OWNER = ? AND COMMENTS IS NOT NULL
```

### Tables actually queried in the dump

Only one M3 table is queried with real data in the dump: **`PFODS.CSYTAB`**
(system constants / code lookups).

```sql
SELECT CTCONO, CTDIVI, CTSTCO, CTSTKY, CTLNCD, CTTX40, CTTX15, CTPARM
  FROM PFODS.CSYTAB
 WHERE CTCONO = 1
   AND (CTLNCD = 'GB' OR CTLNCD = 'SI' OR CTLNCD = ' ' OR CTLNCD IS NULL)
```

Column semantics: `CTCONO` company, `CTDIVI` division, `CTSTCO` constant type,
`CTSTKY` key, `CTLNCD` language, `CTTX40` long desc, `CTTX15` short desc,
`CTPARM` parameter.

No Transfer Order / `MITTRA_AP` / `MGHEAD_AP` / `MPDHED_AP` query exists in the
dump. The full catalog is in `M3_PFODS_Schema_Catalog.xlsx` (binary, not parsed
here) — the new framework's M3 discovery for TO status should start by opening
that xlsx on the company laptop.

### Table categorization heuristic (for filtering the catalog)
- CORE: starts with `M` (MITMAS, MPDHED, MWOHED…) or a letter in
  `C D F O K S E P X Z H W I` followed by 3 alpha chars
- STAGING: prefixes `G02 / G03 / G04` or contains `ERSTELLEN`
- CUSTOM: prefixes `OTPS_, CAPACITY_, DAILY_, PROCESSABLE_, SWB_, QA_, APP_,
  COSTCENTER_, SALES, LANGSTRING_, TOAD_, EDM_`
- VIEW: starts with `VW_` or ends with `_VW`

## EDM (Oracle)

- Host/port/service: `sgp01.sg.pepperl-fuchs.com:1521/SGP01EDMEWA.WORLD`
- Schema: `ADMEDP`
- Auth: `oracledb.connect(dsn=..., externalauth=True)` — Windows integrated
  auth, must run inside a copy of `python.exe` renamed to `EDMAdmin.exe` to
  pass `SYS.PF_SEC_LOGON_TRIGGER` (already documented in CLAUDE.md)

### Tables used

| Table | Columns referenced | Use |
|---|---|---|
| `ADMEDP.EDM_REFERENCES` | `REF` (part number), `DOCNUMBER` | PT → document mapping |
| `ADMEDP.EDM_DOCS` | `DOCNUMBER` (e.g. `PRSG-xxxxx`), `RELEASESTATE` | Release-state lookup |

### PRSG lookup pattern (Pilot_DMR_Report.py)
Filter `DOCNUMBER LIKE 'PRSG-%'`; `RELEASESTATE = 9` means **Released**, any
other value is **Not Released**. Queries are batched in chunks of ~500 PTs per
`IN (...)` clause.

## Working-day / holiday calendar

Singapore public holidays are hardcoded in `jira_npi_standup_daily_with_done`
and used for KPI elapsed-day math:

- 2025: Jan 1; Jan 29–30 (CNY); Apr 1; Apr 18; May 1 (Vesak); Jun 17 (Raya);
  Aug 9; Nov 3; Dec 25
- 2026: Jan 1; Feb 17–18 (CNY); Mar 23; Apr 3; May 1; May 25 (Vesak);
  Jun 8 (Raya); Aug 10; Oct 22 (Diwali); Dec 25

Working days = Mon–Fri minus holidays.

## Scheduling / output destinations

| Script | Schedule | Output |
|---|---|---|
| Pilot_DMR_Report.py | Manual or scheduled; CLAUDE.md says daily 10:00 | Confluence 560866215 + XLSX backup |
| live_kpi.py | Daily 09:30 via Windows Task Scheduler | `kpi_cache.json` → Confluence attachment on 572629046 (Tampermonkey userscript consumes it) |
| expressops_kpi.py | Weekly Monday 10:00 (per CLAUDE.md) | Confluence 560871424 |
| jira_npi_standup_daily_with_done | Manual (interactive prompts) | `~/JiraExports/JIRA_NPI_Standup_YYYYMMDD_HHMMSS.xlsx` |
| comment extractor.py | Manual | `~/JiraExports/JIRA_Container_Comments_YYYYMMDD_HHMMSS.xlsx` |
| Open containers during set date.py | Manual | `~/JiraExports/JIRA_Active_Containers_YYYYMMDD_HHMMSS.xlsx` |
| Jira_Containers_Created_Date.py.py | Manual | `All_Singapore_NPI_History_YYYYMMDD.xlsx` |
| m3_schema_dump.py | Manual | Confluence 572178383 + XLSX backup |
| Kanban Meeting Assistant v2.8 | Browser (Tampermonkey) | JIRA comments posted client-side |

## Secrets — state of play in the dump

All of the following are **hardcoded as plaintext string literals** in one or
more dump scripts (values not reproduced here):

- `JIRA_PAT_TOKEN` — Pilot_DMR_Report, live_kpi, npi_standup, comment extractor, others
- `CONFLUENCE_PAT` — Pilot_DMR_Report, live_kpi, m3_schema_dump
- `CLAUDE_API_KEY` — npi_standup

`excel_to_jira_V4_FIXED.py` is the one script that reads its token from a local
`config.json` (or prompts the user) rather than hardcoding it.

The new framework's mandate (per CLAUDE.md) is `config/config.yaml`, gitignored.
This file is the catalog of what needs to move into that config.
