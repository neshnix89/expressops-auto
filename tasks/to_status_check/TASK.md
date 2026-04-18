# Task: TO Status Check

## Purpose
Check Transfer Order (TO) numbers from JIRA Work Containers against M3 ERP.
If the TO status in M3 is 90 (completed), flag the JIRA container for closing.

## Category
General

## Trigger
On-demand (manual run). May become daily scheduled once proven reliable.

## Systems Involved
- [x] JIRA — **read** — pull active Work Containers and extract TO numbers
- [x] M3 ERP (ODBC) — **read** — check TO status
- [ ] EDM Oracle — not needed
- [ ] Confluence — not needed (output to console/log for now)

## Input
All active NPI Work Containers in JIRA. Containers span many project keys
(USRE, POSX, LCUSAMB, NPIOTHER, SILED2, …) so we scope by issue type plus the
Order Type custom field (`customfield_13905`) rather than by project.

JQL: `issuetype = "Work Container" AND "Order Type" is not EMPTY AND status != Closed`

## Logic
1. Fetch all active Work Containers from JIRA.
2. For each container, extract the TO number.
3. For each TO number, query M3 to check if status = 90.
4. Categorize containers:
   - **READY TO CLOSE:** TO status = 90 in M3
   - **STILL OPEN:** TO status != 90
   - **NO TO:** Container has no TO number assigned
   - **ERROR:** TO number not found in M3
5. Print summary table to console.
6. If `--auto-close` flag is set, transition READY TO CLOSE containers in JIRA. (FUTURE — do not implement yet.)

## Output
Console table:
```
Container    TO Number    M3 Status    Action
EXPOPS-123   7654321      90           READY TO CLOSE
EXPOPS-456   7654322      60           Still open
EXPOPS-789   —            —            No TO number
```

## Fields & Data Mapping

### JIRA Fields
| Field | Custom Field ID | Purpose |
|-------|----------------|---------|
| TO Number | **UNKNOWN** | Where is the TO number stored? |
| Status | status | Current container status |
| Summary | summary | Container title |

### M3 Tables
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| **UNKNOWN** | **UNKNOWN** | TO header with status field |

## Discovery Notes
These MUST be resolved before M3 integration can be built.

- [ ] **Where is the TO number in JIRA?** Check a sample container (e.g., EXPOPS-xxx) — is it in summary, description, a custom field, or a comment? Open a container in JIRA and look.
- [ ] **Which M3 table holds TO data?** Try exploring: `MITTRA_AP`, `MGHEAD_AP`, or similar. Use `M3Client.explore_table()` or `M3Client.get_table_columns()` on the company laptop.
- [ ] **What column holds TO status?** Look for columns with PH prefix containing status values.
- [ ] **What does status 90 mean?** Confirm: 90 = goods received / completed? Are there other meaningful statuses (e.g., 80 = in transit, 99 = cancelled)?
- [ ] **What is the TO number format?** Pure numeric? Prefixed? How many digits?

### Discovery Script
Run this on the company laptop to start exploring:
```python
import sys
sys.path.insert(0, ".")
from core.config_loader import load_config
from core.m3 import M3Client

config = load_config(mode_override="live")
m3 = M3Client(config)

# Try common M3 transfer order tables
for table in ["MITTRA_AP", "MGHEAD_AP", "MHDISP_AP", "MITFAC_AP"]:
    try:
        cols = m3.get_table_columns(table)
        print(f"\n{table}: {len(cols)} columns")
        print(cols[:20])  # First 20 columns
    except Exception as e:
        print(f"\n{table}: NOT FOUND — {e}")

m3.close()
```

## Edge Cases
- Container has TO number in description text (needs regex extraction)
- TO number exists in M3 but with unexpected status value
- Multiple TO numbers per container
- Container already closed in JIRA but TO still shows active in M3

## Mock Data Needed
- [ ] JIRA search result: all active Work Containers (50 sample containers)
- [ ] M3 query: TO status lookup for sample TO numbers
- [ ] Sample of one full JIRA container (all fields) to find where TO is stored

## Acceptance Criteria
- [ ] Correctly extracts TO numbers from JIRA containers
- [ ] Correctly looks up TO status in M3
- [ ] Categorizes containers accurately (matches manual check)
- [ ] Runs in --mock mode on VPS with saved data
- [ ] Runs in --live mode on company laptop
- [ ] Output is clear and actionable
