# Audit Rules — How to Add or Edit Rules

This file explains how to add, edit, or disable audit checks for the
`container_template_audit` task **without touching any Python code**.

All rules live in one file:
```
expressops-auto/config/audit_rules.yaml
```

---

## Rule Structure

Each rule looks like this:

```yaml
- id: your_rule_name          # unique name, no spaces, use underscores
  enabled: true               # true = active, false = skip this rule
  severity: ERROR             # ERROR or WARNING
  check: check_type           # see Check Types below
  message: "What went wrong"  # shown in audit report and Confluence
  fix_hint: "How to fix it"   # shown as → hint under the message
  # + extra fields depending on check type (see below)
```

---

## Check Types

### 1. `description_count`
Checks how many times a keyword appears in the description.
Use this to catch duplicated template sections.

```yaml
- id: duplicate_template
  enabled: true
  severity: ERROR
  check: description_count
  keyword: "NPI Overview"       # word to count
  threshold: 1                  # expected count — flags if MORE than this
  message: "NPI template deployed {count} times — should be exactly 1."
  fix_hint: "Delete duplicate template copies using Visual mode in Jira."
```

---

### 2. `description_field_empty`
Checks if a named row in the NPI Overview table has no value filled.
The field_label must match exactly what appears in the left column of the table.

```yaml
- id: missing_imr
  enabled: true
  severity: WARNING
  check: description_field_empty
  field_label: "IMR / HIM number"    # exact text from the table row
  message: "IMR/HIM number is empty — required for transport via DO."
  fix_hint: "Fill in IMR/HIM number in the NPI Overview table."
```

---

### 3. `jira_field_missing`
Checks if a JIRA custom field is blank on the container.
Use the confirmed field IDs from REF_JIRA.md.

```yaml
- id: missing_location
  enabled: true
  severity: ERROR
  check: jira_field_missing
  field_id: "customfield_13906"      # NPI Location
  field_name: "NPI Location"         # human-readable name for the message
  message: "NPI Location not set."
  fix_hint: "Set NPI Location during the Request step."
```

---

## How to Add a New Rule

1. Open `config/audit_rules.yaml` in Notepad
2. Copy any existing rule block as a template
3. Change the `id` to something unique
4. Set `enabled: true`
5. Pick the right `check` type from the list above
6. Fill in `message` and `fix_hint`
7. Save the file

That's it. No Python changes needed. Next time the audit runs, the new rule is active.

---

## How to Disable a Rule

Change `enabled: true` to `enabled: false`. The rule stays in the file
but is skipped during the audit. Useful for rules that are causing false
positives or are temporarily not relevant.

---

## How to Change Severity

Change `severity: WARNING` to `severity: ERROR` (or vice versa).

- **ERROR** — shown as ❌, counts as a blocking issue
- **WARNING** — shown as ⚠️, counts as a non-blocking flag

---

## Important Notes

- **Do not change the `id`** of an existing rule once it's live —
  the ignore table in Confluence uses rule IDs to track which issues
  were acknowledged. Changing an ID will cause it to re-trigger.
- **Indentation matters in YAML** — always use spaces, never tabs.
  Each line inside a rule must be indented with 2 spaces.
- **Test after adding** — run a quick check on a known container:
  ```
  scripts\run_container_audit.bat NPIOTHER-XXXX
  ```
  If you see a Python error, check your YAML indentation first.

---

## Example: Full audit_rules.yaml

```yaml
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

  - id: missing_pcb_info
    enabled: false
    severity: WARNING
    check: description_field_empty
    field_label: "PCB order status"
    message: "PCB order status not filled in Focus Material table."
    fix_hint: "Add PCB order status in the Focus Material table."

  - id: missing_location
    enabled: true
    severity: ERROR
    check: jira_field_missing
    field_id: "customfield_13906"
    field_name: "NPI Location"
    message: "NPI Location not set."
    fix_hint: "Set NPI Location during the Request step."
```
