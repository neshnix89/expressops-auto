# Task: [TASK NAME]

## Purpose
[One sentence: What does this task do and why?]

## Category
[General / MR / Clocking]

## Trigger
[When does this run? Daily? On-demand? On container creation?]

## Systems Involved
- [ ] JIRA — [read/write] — [what data?]
- [ ] M3 ERP (ODBC) — [read/write] — [what data?]
- [ ] EDM Oracle — [read/write] — [what data?]
- [ ] Confluence — [read/write] — [what page?]
- [ ] Other: [specify]

## Input
[What triggers the task? A list of JIRA container keys? A date range? Manual invocation?]

## Logic
[Step-by-step: what does the script do?]
1. Fetch [X] from [system]
2. Compare/calculate [Y]
3. Output [Z] to [destination]

## Output
[What does the user see? A Confluence table? A console report? Updated JIRA fields? An Excel file?]

## Fields & Data Mapping

### JIRA Fields
| Field | Custom Field ID | Purpose |
|-------|----------------|---------|
| [field] | customfield_XXXXX | [purpose] |

### M3 Tables
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| [table_AP] | [columns] | [purpose] |

### EDM Tables
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| [table] | [columns] | [purpose] |

## Discovery Notes
[Document unknowns here. What tables/fields haven't been identified yet?
What needs manual investigation on the company laptop before coding can begin?]

- [ ] [Unknown 1: e.g., "Which M3 table holds TO status 90?"]
- [ ] [Unknown 2: e.g., "What JIRA field stores the TO number?"]

## Edge Cases
[What could go wrong? Missing data? Duplicate entries? Permission issues?]

## Mock Data Needed
[List the API calls or queries whose responses should be saved for VPS testing]
- [ ] [e.g., "JIRA search: all Pilot Run containers in EXPRESSOPS project"]
- [ ] [e.g., "M3 query: MPDHED_AP for sample TO numbers"]

## Acceptance Criteria
[How do you know this task works correctly?]
- [ ] [e.g., "Correctly identifies containers where TO status = 90"]
- [ ] [e.g., "Matches what I see when I check manually in M3"]
