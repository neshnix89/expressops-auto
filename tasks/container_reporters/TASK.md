# Task: Container Reporters (Reporter + Resolved date export)

## Purpose
Pull the Reporter and the Resolved date for NPI Work Containers out of JIRA, using
the same container filter the KPI overlay uses, and write them to a CSV.

## Category
General

## Trigger
On-demand — `run_container_reporters.bat` on the company laptop. Not scheduled.

## Systems Involved
- [x] JIRA — read only — Work Container issues (system fields + NPI custom fields)
- [ ] M3 ERP / EDM / Confluence — not used. This task writes nothing anywhere.

## Input
Optional CLI flags: `--scope`, `--since`, `--until`.

## Logic
1. Build the JQL (`logic.build_jql`) from the KPI-overlay container filter plus a
   resolution clause chosen by `--scope`.
2. `jira.search_all` with paging, requesting only the fields the CSV needs.
3. Flatten each issue to a row (`logic.issue_row`), sorted by resolved date.
4. Write `outputs/container_reporters.csv` and log a per-reporter tally.

## Output
- `outputs/container_reporters.csv` — one row per container, UTF-8 with BOM so
  Excel opens it correctly.
- `logs/container_reporters.log` — the JQL, the counts, the per-reporter tally.

Columns: `issueKey, reporter, reporterUser, reporterEmail, resolvedDate,
resolvedTimestamp, resolution, status, location, orderType, created, ptDocument,
summary`.

## Filtering — relationship to the KPI overlay
The overlay's JQL (`tasks/kpi_overlay/main.py` `OPEN_WC_JQL`) is:

```
issuetype = "Work Container"
AND "Product Type" = "SMT PCBA"
AND "NPI Location" in ("Singapore", "Trutnov")
AND resolution is EMPTY
```

This task keeps the first three clauses verbatim — same population, same two
locations — and varies only the last one, because `resolution is EMPTY` is
exactly the set that can never have a resolved date:

| `--scope`  | resolution clause          | meaning |
|------------|----------------------------|---------|
| `resolved` | `resolution is not EMPTY`  | default; every row has a resolved date |
| `open`     | `resolution is EMPTY`      | the overlay's exact set; resolved date always blank |
| `all`      | *(none)*                   | both, open rows sort last with a blank resolved date |

`--since` / `--until` are inclusive bounds on `resolutiondate` (YYYY-MM-DD).
`--until` is sent as `<= "<date> 23:59"` so the whole end day is included.

## Fields & Data Mapping

### JIRA Fields
| Field | ID | Purpose |
|-------|----|---------|
| Reporter | `reporter` (system) | who raised the container — display name, username, email |
| Resolved | `resolutiondate` (system) | the resolved date; date part + raw timestamp |
| Resolution | `resolution` (system) | Done / Cancelled / … — distinguishes finished from dropped |
| Status | `status` (system) | current workflow status |
| Created | `created` (system) | for age/lead-time work later |
| Product Type | `customfield_13904` | filter: "SMT PCBA" |
| NPI Location | `customfield_13906` | filter: Singapore / Trutnov; also a CSV column |
| Order Type | `customfield_13905` | Pilot Run / DMR Request / QS … |
| PTxx Document | `customfield_13907` | project reference |

## Edge Cases
- **Reporter missing** — deleted or inactive JIRA account. The row is kept with a
  blank reporter and the keys are logged as a WARNING; dropping the container
  would silently shrink the count.
- **Resolved then reopened** — JIRA clears `resolutiondate`, so the container
  moves from `resolved` to `open` scope. Expected, not a bug.
- **Timezones** — Trutnov timestamps carry +0200, Singapore +0800. `resolvedDate`
  is the date as JIRA reports it in the issue's own offset (via
  `kpi_core.to_date`), which is what a local reader expects.
- **Large result sets** — `search_all` pages at 200; a multi-year `--scope all`
  pull is thousands of issues and takes a minute or two.

## Mock Data Needed
- [x] `mock_data/containers.json` — 4 hand-written containers (SG + Trutnov, one
  still open, one with no reporter) so every branch runs on the VPS.

## Acceptance Criteria
- [x] `python -m tasks.container_reporters.main --mock` writes a 3-row CSV
      (default `resolved` scope drops the open container).
- [x] `--scope all` includes the open container with a blank resolved date.
- [x] A malformed `--since` fails with a one-line `[ERROR]`, no traceback.
- [ ] Live run: row count matches the same JQL pasted into the JIRA issue search.
