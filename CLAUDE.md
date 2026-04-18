# ExpressOPS Automation Framework

## What This Is
Modular automation suite for Express Operations NPI (New Product Introduction) at Pepperl+Fuchs Singapore.
Each task is a standalone Python module that queries enterprise systems (JIRA, M3, EDM, Oracle), performs logic checks or data transformations, and outputs results to Confluence, Excel, or JIRA.

## Architecture

### Environment Split
- **VPS (development):** Claude Code writes and tests code here using `--mock` mode with saved sample data.
- **Company laptop (execution):** Has live access to JIRA, M3 (ODBC), EDM (Oracle), Confluence. Pulls code via Git, runs with `--live` mode. Project lives at `C:\Users\tmoghanan\Documents\AI\expressops-auto`.
- **GitHub private repo:** Bridge between VPS and company laptop.

### Key Principle
Every task script MUST support two modes:
- `--mock` : Reads from `tasks/<task_name>/mock_data/` — used for VPS development and CI testing.
- `--live` : Connects to real systems — only runs on company laptop.

The mode is controlled via config or CLI flag. **Never hardcode credentials or connection strings.**

---

## Project Layout

```
expressops-auto/
├── CLAUDE.md                  ← You are here. Read this every session.
├── README.md                  ← Setup and usage instructions
├── requirements.txt           ← Shared Python dependencies
├── ops.bat                    ← Company laptop CLI runner (Windows)
├── config/
│   ├── config.example.yaml   ← Template with structure, no secrets
│   └── config.yaml           ← Real config with secrets (GITIGNORED)
├── core/                     ← Shared modules
│   ├── __init__.py
│   ├── jira_client.py        ← JIRA REST API wrapper (on-prem, PAT auth)
│   ├── confluence.py         ← Confluence read/write via REST API
│   ├── edm.py                ← Oracle EDM connector (via EDMAdmin.exe)
│   ├── m3.py                 ← M3 ERP via ODBC (DSN: ODSSG, schema: PFODS)
│   ├── config_loader.py      ← Loads config.yaml, validates required fields
│   └── logger.py             ← Standardized logging for all tasks
├── tasks/
│   └── <task_name>/
│       ├── TASK.md            ← Spec: what, why, systems, fields, logic
│       ├── main.py            ← Entry point: parse args, run task
│       ├── logic.py           ← Pure business logic (testable without APIs)
│       └── mock_data/         ← Saved JSON/CSV from real API responses
├── scripts/
│   ├── capture_mock_data.py  ← Run on company laptop to save API responses
│   └── setup_env.bat         ← One-time company laptop environment setup
├── dashboard/                ← Phase 3: status overview (build later)
└── docs/
    └── TASK_TEMPLATE.md      ← Copy this when creating a new task
```

---

## System Access Details

### JIRA (On-Premises)
- **Base URL:** `https://pfjira.pepperl-fuchs.com`
- **Auth:** PAT Bearer token in `Authorization: Bearer <token>` header
- **SSL:** `verify=False` (self-signed cert)
- **API:** REST v2 — `/rest/api/2/issue/{key}`, `/rest/api/2/search`
- **Projects:** Work Containers span multiple JIRA project keys (USRE, POSX, LCUSAMB, NPIOTHER, SILED2, …). There is no single "EXPRESSOPS" project. Filter by issue type + custom field (e.g. Order Type), not by project key.
- **Key custom fields** (confirmed against the live JIRA field API):
  - `customfield_13300` = EDM Document Number
  - `customfield_13502` = M3 Article Number
  - `customfield_13700` = Project Status
  - `customfield_13903` = Request Type
  - `customfield_13904` = Product Type (e.g. "SMT PCBA")
  - `customfield_13905` = Order Type (e.g. "Pilot Run", "DMR Request")
  - `customfield_13906` = NPI Location (e.g. "Singapore", "Trutnov")
  - `customfield_13907` = PTxx Document — **not** "Project ID"
  - `customfield_15009` = Work Container NPI Status Light
  - `customfield_15400` = NPI WC Status
  - `customfield_15800` = Issue_parked_log (timestamped START/END entries; previously mis-labelled "ParkingLog")
  - `customfield_15805` = Component Part Number
- **Parent-child:** Use `relation()` JQL to fetch child Work Packages from Work Containers.
- **WP name matching:** Always case-insensitive. Inconsistent casing is normal.
- **Timestamp parsing:** Strip milliseconds and timezone offsets via regex before datetime parsing.

### M3 ERP (ODBC)
- **DSN:** `ODSSG`
- **Schema:** `PFODS`
- **Connection:** `pyodbc.connect("DSN=ODSSG")` — no timeout parameter.
- **Tables:** Suffix `_AP` (e.g., `MITMAS_AP`, `MPDHED_AP`, `MPDOPE_AP`)
- **Column prefixes:** PH-prefixed columns; positional `?` parameters for queries.
- **No REST API.** M3 uses session-based servlets. ODBC is the viable path.

### EDM Oracle
- **Schema:** `ADMEDP`
- **Key table:** `ADMEDP.EDM_REFERENCES`
- **Access quirk:** Python must run via renamed executable `EDMAdmin.exe` (copy of `python.exe`) to bypass `SYS.PF_SEC_LOGON_TRIGGER`.

### Confluence
- **Base URL:** `https://pfteamspace.pepperl-fuchs.com`
- **Space:** `EUDEMHTM0021`
- **Auth:** PAT Bearer token
- **Publishing:** BeautifulSoup for HTML manipulation; `ac:structured-macro` for status badges.
- **Critical rule:** Always read existing page content before writing — preserve manual team edits (PIC feedback columns, Handover fields, Remarks, MR Status).

---

## Coding Standards

### Every task module must:
1. Accept `--mock` / `--live` CLI argument (default: `--mock` for safety).
2. Use `core/config_loader.py` for all credentials and connection strings.
3. Log to both console and `logs/<task_name>.log` via `core/logger.py`.
4. Handle errors gracefully — log the error, continue if possible, never crash silently.
5. Separate business logic (`logic.py`) from I/O (`main.py`) so logic is testable with mock data.

### Code style:
- Python 3.12 compatible.
- Type hints on function signatures.
- Docstrings on public functions.
- No hardcoded secrets, URLs, or paths — all from config.yaml.

### Git workflow:
- `main` branch = stable, runs on company laptop via Task Scheduler.
- Feature branches for new tasks: `task/<task-name>`.
- Commit messages: `[task-name] description` or `[core] description`.

---

## How to Build a New Task

1. Copy `docs/TASK_TEMPLATE.md` to `tasks/<task_name>/TASK.md`.
2. Fill in the spec: what the task does, systems involved, fields needed, expected output.
3. If data sources are unknown, add a **Discovery** section documenting what needs investigation.
4. Implement `main.py` and `logic.py`.
5. Capture mock data on company laptop: `python scripts/capture_mock_data.py --task <task_name>`.
6. Test on VPS: `python tasks/<task_name>/main.py --mock`.
7. Test on company laptop: `ops test <task_name>` then `ops run <task_name>`.
8. Add to Windows Task Scheduler if recurring.

---

## Existing Automations (Reference)

These predate this framework and run independently. They may be migrated later:

- **MR Status Report** (`Pilot_DMR_Report.py`) — daily 10:00 AM, publishes to Confluence page 560866215.
- **ExpressOPS KPI Pipeline** (`expressops_kpi.py`) — Monday 10:00 AM, publishes to Confluence page 560871424.
- **ALMA-T** — Shelved. Replaced by this modular approach.

---

## For Claude Code: Session Rules

- **Always read this file first** at the start of every session.
- **Read the relevant TASK.md** before modifying any task code.
- **Never commit credentials** — config.yaml is gitignored.
- **Ask before writing to live systems** — mock mode is the default for a reason.
- **If a data source is unknown**, say so and add it to the Discovery section of TASK.md. Do not guess table names or field names.
