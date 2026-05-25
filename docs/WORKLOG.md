# WORKLOG — container_template_audit batch system
# Date: 2026-05-25
# Status: BUILT — ready for company laptop testing

## Objective

Extend the existing `tasks/container_template_audit/main.py` (single container
audit) into a full batch system that:
1. Audits all active SMT PCBA Singapore containers daily
2. Publishes results to Confluence page 592255806
3. Generates editable draft JIRA comments on Confluence
4. Allows one-command comment push to JIRA with reporter tagged
5. Allows one-command ignore of containers (moved to ignore table, never re-checked)
6. Loads additional rules from `config/audit_rules.yaml`

---

## Files to Create

```
tasks/container_template_audit/
├── main.py          ← ALREADY EXISTS — do not modify
├── batch.py         ← CREATE
├── comment_push.py  ← CREATE
└── ignore.py        ← CREATE

config/
└── audit_rules.yaml ← CREATE

scripts/
├── run_audit_batch.bat      ← CREATE
├── push_audit_comment.bat   ← CREATE
└── ignore_container.bat     ← CREATE
```

---

## 1. config/audit_rules.yaml

Create this file with the initial rule set:

```yaml
# NPI Container Audit Rules
# Add new rules here without touching Python code.
# See tasks/container_template_audit/AUDIT_RULES_README.md for full guide.

rules:

  - id: duplicate_template
    enabled: true
    severity: ERROR
    check: description_count
    keyword: "NPI Overview"
    threshold: 1
    message: "NPI template deployed {count} times — should be exactly 1."
    fix_hint: "Delete duplicate copies in description using Visual mode in Jira."

  - id: missing_imr
    enabled: true
    severity: WARNING
    check: description_field_empty
    field_label: "IMR / HIM number"
    message: "IMR/HIM number is empty — required for transport via DO."
    fix_hint: "Fill in IMR/HIM number in the NPI Overview table."

  - id: missing_cost_center
    enabled: true
    severity: WARNING
    check: description_field_empty
    field_label: "Cost center"
    message: "Cost center is empty in NPI Overview table."
    fix_hint: "Fill in the cost center to which this NPI run shall be billed."

  - id: missing_requestor_name
    enabled: true
    severity: WARNING
    check: description_field_empty
    field_label: "Name of Requestor"
    message: "Name of Requestor is empty in NPI Overview table."
    fix_hint: "Fill in the name of the person formally requesting the NPI in Jira."

  - id: missing_location
    enabled: true
    severity: ERROR
    check: jira_field_missing
    field_id: "customfield_13906"
    field_name: "NPI Location"
    message: "NPI Location not set."
    fix_hint: "Set NPI Location during the Request step."

  - id: missing_product_type
    enabled: true
    severity: ERROR
    check: jira_field_missing
    field_id: "customfield_13904"
    field_name: "Product Type"
    message: "Product Type not set."
    fix_hint: "Set Product Type during the Request step."

  - id: missing_order_type
    enabled: true
    severity: ERROR
    check: jira_field_missing
    field_id: "customfield_13905"
    field_name: "Order Type"
    message: "Order Type not set (DS/QS/PT/PR/DMR)."
    fix_hint: "Set Order Type during the Request step."
```

---

## 2. tasks/container_template_audit/batch.py

### Purpose
- Fetch all active SMT PCBA Singapore containers (same JQL as other tasks)
- Load ignore list from Confluence page 592255806 (read ignore table first)
- For each non-ignored container, run the audit using the same logic as main.py
- Also run YAML rules from audit_rules.yaml on top
- Publish full results to Confluence page 592255806
- Two subcommands: `scan` (read-only, Confluence publish only) and `comment --keys KEY1 KEY2` (post to JIRA)

### JQL (same as rest of expressops-auto tasks)
```python
jql = (
    'issuetype = "Work Container" '
    'AND "Product Type" = "SMT PCBA" '
    'AND "NPI Location" = "Singapore" '
    'AND resolution is EMPTY '
    'ORDER BY created ASC'
)
```

### Config loading
```python
# Load config from config/config.yaml (same path logic as main.py load_file())
# Load audit rules from config/audit_rules.yaml
# Use PyYAML: import yaml
```

### YAML rule engine
Implement a `run_yaml_rules(issue_fields, description, rules)` function:
- `description_count`: count keyword in description, flag if count > threshold
  - message supports `{count}` placeholder
- `description_field_empty`: search for `field_label` text in description HTML,
  check if the adjacent cell is empty or contains only whitespace/dashes
- `jira_field_missing`: check if fields.get(field_id) is None or empty string

Each YAML rule finding uses same Finding dataclass as main.py.

### Confluence page structure (page ID: 592255806)

Publish as full HTML replacement. Structure:

```
[Header section]
Last run: DD-Mon-YYYY HH:MM | X containers checked | ❌ N errors | ⚠️ N warnings | ✅ N clean

[Table 1: CONTAINERS WITH ISSUES]
Columns: Container | Summary | Status | Issues Found | Draft Comment

[Table 2: IGNORED CONTAINERS]
Columns: Container | Summary | Ignored On | Reason
```

#### Table 1 rules:
- Only show containers that have at least 1 ERROR or WARNING
- Container column: clickable link to JIRA issue
- Issues Found column: list each finding as `❌ message` or `⚠️ message`, one per line using `<br/>`
- Draft Comment column: pre-filled editable text area style cell with the draft comment text
  - Draft comment format (see below)
  - This cell is the one humans edit before pushing
  - Preserve existing draft comment text if container already exists in table
    (read back current content before overwrite — same pattern as mo_trigger_comment)
  - Add duplicate guard check: if `#Ref: AuditCheck#` already exists in JIRA
    comments for this container, show "Already posted" instead of draft

#### Table 2 rules:
- Read existing ignore table before publishing — preserve all existing rows
- Never remove rows from ignore table during publish
- Containers in ignore table are skipped during audit entirely

### Draft comment format
```
Hi [~{reporter_name}],

We noticed the following on your NPI container *{key}* during our routine audit:

{list of findings — each as ❌ or ⚠️ + message}

Please review and update the container, or let us know if you need help.

#Ref: AuditCheck#
```

Reporter name: use `reporter["name"]` from JIRA issue fields (same as other tasks).

### Confluence HTML helpers
Reuse the same `esc()`, `_cell()`, `_header_cell()` patterns from other tasks.
Always HTML-escape all user data before embedding in HTML.
Do not use ac:structured-macro for this page — plain HTML tables only.

### CLI interface
```
python batch.py scan                    # audit all, publish to Confluence
python batch.py scan --dry-run          # audit all, print results, no publish
python batch.py comment --keys K1 K2   # post draft comment from Confluence to JIRA
```

---

## 3. tasks/container_template_audit/comment_push.py

### Purpose
Read the draft comment for a specific container from Confluence page 592255806,
then post it to JIRA with duplicate guard.

### Logic
1. Load config (same load_file() pattern)
2. Fetch Confluence page 592255806
3. Parse issues table — find row where Container = args.key
4. Extract draft comment text from that row's Draft Comment cell
5. Check JIRA issue comments — if `#Ref: AuditCheck#` already exists, abort with message
6. Post comment to JIRA: `POST /rest/api/2/issue/{key}/comment`
7. Print confirmation

### CLI
```
python comment_push.py NPIOTHER-123
python comment_push.py NPIOTHER-123 --dry-run   # print comment, don't post
```

---

## 4. tasks/container_template_audit/ignore.py

### Purpose
Move a container from the issues table to the ignore table on Confluence.

### Logic
1. Load config
2. Fetch Confluence page 592255806
3. Read current page HTML
4. Find the container row in issues table — remove it
5. Add a new row to ignore table: Key | Summary | today's date | reason (from args)
6. Update Confluence page (version + 1)
7. Print confirmation

### CLI
```
python ignore.py NPIOTHER-123
python ignore.py NPIOTHER-123 --reason "Container already in SMT Build phase"
```

---

## 5. Bat files in scripts/

### run_audit_batch.bat
```bat
@echo off
set PYTHONIOENCODING=utf-8
set PYTHONWARNINGS=ignore
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe tasks\container_template_audit\batch.py %*
```

### push_audit_comment.bat
```bat
@echo off
set PYTHONIOENCODING=utf-8
set PYTHONWARNINGS=ignore
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe tasks\container_template_audit\comment_push.py %*
```

### ignore_container.bat
```bat
@echo off
set PYTHONIOENCODING=utf-8
set PYTHONWARNINGS=ignore
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe tasks\container_template_audit\ignore.py %*
```

---

## 6. Dependencies

PyYAML is required for loading audit_rules.yaml.
Check if already installed: `python -m pip show pyyaml`
Install if missing: `pip install pyyaml --break-system-packages`

All other dependencies (requests, urllib3) already present in environment.

---

## Key Constants (confirmed from expressops-auto project)

```python
JIRA_BASE       = "https://pfjira.pepperl-fuchs.com"
CONFLUENCE_BASE = "https://pfteamspace.pepperl-fuchs.com"
AUDIT_PAGE_ID   = "592255806"
CONFIG_PATH     = "config/config.yaml"   # relative to expressops-auto root
RULES_PATH      = "config/audit_rules.yaml"
GUARD_MARKER    = "#Ref: AuditCheck#"

# Auth: Bearer PAT, verify=False (on-prem self-signed cert)
# JIRA PAT from config.yaml: jira.pat
# Confluence PAT from config.yaml: confluence.pat

# JIRA custom fields (confirmed):
# customfield_13905 = Order Type
# customfield_13904 = Product Type
# customfield_13906 = NPI Location
# customfield_13903 = Request Type
# customfield_13907 = PTxx Document
# customfield_15400 = NPI WC Status
# customfield_15800 = Issue_parked_log

# Reporter mention syntax: [~reporter["name"]]
# Children JQL: relation("{key}", "Project Children", Tasks, Deviations, level1)
# Always filter parent self-reference: wp["key"] != parent_key
```

---

## Testing sequence

1. `pip install pyyaml --break-system-packages`
2. `scripts\run_audit_batch.bat scan --dry-run` — confirm audit runs, no Confluence write
3. `scripts\run_audit_batch.bat scan` — confirm Confluence page 592255806 is updated
4. Check page in browser — verify issues table and ignore table render correctly
5. `scripts\push_audit_comment.bat NPIOTHER-XXXX --dry-run` — confirm correct comment printed
6. `scripts\ignore_container.bat NPIOTHER-XXXX --reason "Already in SMT Build"` — confirm row moves
7. Run `scan` again — confirm ignored container no longer appears in issues table

---

## System Reference Updates

### REF_JIRA.md
- nothing new

### REF_CONFLUENCE.md
- New page ID: 592255806 — container_template_audit dashboard

### REF_M3.md
- nothing new

### REF_EDM.md
- nothing new
