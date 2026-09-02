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
Optional CLI flags: `--source`, `--scope`, `--since`, `--until`, `--all-dates`.

## Logic
1. Build the JQL for the chosen `--source` (`logic.build_jql`, or
   `lineage_jql` + `parents_jql` for `template`) plus a resolution clause from
   `--scope` and the resolved-date window.
2. `jira.search_all` with paging, requesting only the fields the CSV needs;
   `template` dedupes containers across key batches.
3. Flatten each issue to a row (`logic.issue_row`), sorted by resolved date.
4. Write `outputs/container_reporters.csv` and log a per-reporter tally.

## Output
- `outputs/container_reporters.csv` — one row per container, UTF-8 with BOM so
  Excel opens it correctly.
- `logs/container_reporters.log` — the JQL, the counts, the per-reporter tally.

Columns: `issueKey, issueType, parentKey, reporter, reporterUser, reporterEmail,
resolvedDate, resolvedTimestamp, resolution, status, location, orderType,
requestType, created, ptDocument, summary`.

## Container level only
The JQL asks for `issuetype = "Work Container"`, so Work Packages are never
requested — they are a different issue type, fetched by the overlay through a
separate `relation(..., "Project Children", level1)` query that this task does
not run. Two things make that checkable instead of assumed:

- `issueType` and `parentKey` are columns. A container reads
  `Work Container` with an empty parent; a Work Package would show its own type
  and its container's key, so one glance at the sheet settles it.
- The run logs a tally by issue type, and any row that is not a parentless
  `Work Container` is listed as a WARNING (`logic.non_containers`).

## Filtering

### `--source template` (default) — the NPI family, closed containers included
Filter 25423 selects work packages whose status is Waiting / In Progress /
Backlog. A container leaves that filter the moment its last work package
finishes, so **a fully closed container can never appear in it**. This source is
the same lineage with that one clause removed, run as two queries:

1. **Lineage** — every work package cloned from the eight ITPL templates
   (`logic.lineage_jql`, filter 25423's text minus the status clause):

   ```
   issue in relation("issue in relation('key in (ITPL-769, ITPL-760, ITPL-756,
   ITPL-750, ITPL-746, ITPL-742, ITPL-1036, ITPL-1027)', 'Project Children',
   Tasks, Deviations, level4)", "Project Children", 'Clone from Template',
   level4) and project != 'Issue Template'
   ```

2. **Containers** — those work packages' Project Parents, batched 250 keys at a
   time (`logic.parents_jql`), with the export's own filters applied:

   ```
   issue in relation("key in (<wp keys>)", "Project Parent", Tasks, Deviations, level1)
   AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore"
   AND resolution is not EMPTY AND resolutiondate >= "2025-01-01"
   ```

Two queries, not one, for two reasons: nesting the lineage relation() inside the
Project-Parent relation() needs a third level of quoting and JQL has only `"`
and `'`; and passing the WP *keys* to step 2 does not assume the `parent` field
is populated — the WC/WP hierarchy here is a relation, not a subtask link.

With the default `--scope resolved` this is exactly "containers in the board's
filtering that are fully closed". `--scope all` gives the whole family.

### `--source board` — the Kanban board's own population
The board is driven by saved filter **25423**, which selects *work packages*
(template clones, `project != 'Issue Template'`) whose status is Waiting,
In Progress or Backlog. The export takes those WPs' **Project Parents** — the
containers:

```
issue in relation("filter=25423", "Project Parent", Tasks, Deviations, level1)
AND "Product Type" = "SMT PCBA"
AND "NPI Location" = "Singapore"
```

Singapore only, because that is what the board filter says.

`--source board --scope resolved` is therefore a narrow, odd population — a
container that is closed while a work package is still open — and the run logs
a NOTE pointing at `--source template`. `--source board --scope all` is the
useful form: the board exactly as it stands.

The filter ID and the template keys live in config
(`container_reporters.board_filter`, `.template_keys`), not in the code — board
rebuilds change them.

### `--source overlay` — the KPI overlay's issue-type query
`tasks/kpi_overlay/main.py` `OPEN_WC_JQL` is:

```
issuetype = "Work Container"
AND "Product Type" = "SMT PCBA"
AND "NPI Location" in ("Singapore", "Trutnov")
AND resolution is EMPTY
```

This source keeps the first three clauses verbatim — same population, same two
locations — and varies only the last one, because `resolution is EMPTY` is
exactly the set that can never have a resolved date:

| `--scope`  | resolution clause          | meaning |
|------------|----------------------------|---------|
| `resolved` | `resolution is not EMPTY`  | default; every row has a resolved date |
| `open`     | `resolution is EMPTY`      | the overlay's exact set; resolved date always blank |
| `all`      | *(none)*                   | both, open rows sort last with a blank resolved date |

### Dates
`--since` / `--until` are inclusive bounds on `resolutiondate` (YYYY-MM-DD).
`--until` is sent as `<= "<date> 23:59"` so the whole end day is included.
**`--since` defaults to `2025-01-01`**; `--all-dates` removes the floor.

On `--scope all` the window is OR'd with `resolution is EMPTY`
(`(resolutiondate >= "..." OR resolution is EMPTY)`) — a NULL resolved date
fails every date comparison, so a bare bound would silently delete the open
containers that scope exists to include.

## Fields & Data Mapping

### JIRA Fields
| Field | ID | Purpose |
|-------|----|---------|
| Issue Type | `issuetype` (system) | proof the row is container level |
| Parent | `parent` (system) | blank on containers; set on Work Packages |
| Request Type | `customfield_13903` | "NPI Request" etc. |
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
- **Board filter drift** — filter 25423 is a saved filter someone else owns. If
  it is edited or rebuilt the population changes with no error here; the run
  logs the filter ID so a surprising row count is traceable to it. `--source
  template` reads the template keys from config instead, so it is unaffected by
  edits to the saved filter — and equally will not follow a template added to
  it. The templates are logged every run.
- **A container reachable from several work packages** — normal (a container has
  ~9). Deduped by key across batches.
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
  still open, one with no reporter) plus one Work Package that the JQL would
  never return, so the not-container-level warning path is exercised too.

## Acceptance Criteria
- [x] `python -m tasks.container_reporters.main --mock` writes a 3-row CSV
      (default `resolved` scope drops the open container).
- [x] `--scope all` includes the open container with a blank resolved date.
- [x] A malformed `--since` fails with a one-line `[ERROR]`, no traceback.
- [x] A Work Package in the result is logged as a WARNING and shows its type and
      parent in the CSV.
- [ ] Live run: row count matches the same JQL pasted into the JIRA issue search.
- [ ] Live run: `--source board --scope all` matches the container count on the
      Kanban board itself.
- [ ] Live run: a container known to be fully closed (e.g. NPIOTHER-4681 and its
      WPs NPIOTHER-4707..4714, all Done) appears under `--source template`.
