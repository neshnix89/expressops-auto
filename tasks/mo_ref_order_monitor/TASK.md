# Task: mo_ref_order_monitor

## Purpose
Poll the M3 "Ref order no" field (P1 / PMS100 MO header) every ~15 min for each
active MO, and publish fine-grained build progress to the JIRA Work Container —
replacing the once-a-day Excel→Jira tool with near-real-time updates, Webex
notifications, and a per-stage dwell-time summary. Intended to eventually retire
the Excel→Jira Publisher.

## Category
General

## Trigger
Scheduled poller, every ~15 min (Windows Task Scheduler on company laptop).
Per MO, keep polling until the JIRA container is closed (resolution set).

## Systems Involved
- [x] JIRA — read — container universe (JQL), container comments (MO→container map), current description
- [x] JIRA — write — container **description** (MO BUILD STATUS table + dwell summary)
- [x] M3 ERP (ODBC) — read — MO header: Ref order no, MO status  *(primary path — see Discovery)*
- [ ] M3 ERP (H5 PMS100) — read — **fallback only** if Ref order no is not ODBC-exposed
- [x] Webex — write — stage notifications routed to a group by Ref-order-no value *(see Discovery)*

---

## Confirmed contract inherited from Excel→Jira (src/backend/excel_to_jira.py)

Keep these identical so existing containers stay consistent:

- **Container universe JQL:**
  `issue in relation("filter=25423", "Project Parent", Tasks, Deviations, level1) AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" ORDER BY created ASC`
  (fields: `key, summary`, maxResults 100)
- **MO → container resolution:** the container is the one whose **comments** contain
  the MO-number string. (No M3↔JIRA key link exists; the MO number in a comment is
  the only bridge. `mo_trigger_comment` / manual comments are what put it there.)
- **Write target:** container `description` via `PUT /rest/api/2/issue/{key}`.
- **Table format (JIRA wiki markup), one section per MO:**
  ```
  h2. MO BUILD STATUS - {mo_no}
  ||MO #||PN||MO Nr||Day||PIC||Activity||
  |1|{pn}|{mo_no}|{day}|{pic}|{activity}|
  ...
  _Last updated by {username} on {timestamp}_
  ```
  Rows are **upserted by Day**, sorted by `DD-MMM`, renumbered sequentially.

---

## New behaviour (deltas from Excel→Jira)

1. **Source of "Activity" = M3 Ref order no**, read live per poll (not an Excel cell).
2. **Change detection:** if the Ref order no is unchanged since the last write → no
   update. **Exception:** the first run of each day always writes the current value
   even if unchanged.
3. **MO status gate:**
   - While MO status < 80: publish updates as above.
   - When status turns **80 or 90**: write a final line "MO closed — now Sts {90/80}"
     (ignore Ref order no from that point) **and** publish the dwell-time summary
     (see below) below the MO status table.
   - Keep polling after closure but write nothing — **unless** status drops back
     below 80, then resume normal publishing.
   - Abandon the MO entirely once the JIRA **container is closed** (resolution set).
4. **Dwell-time summary:** for each distinct Ref-order-no value observed, how long it
   stayed before advancing to the next (days + hours). Published once, below the MO
   status table, when status → 80/90. This is a delay indicator.
5. **Webex notification:** on each published change, send to a Webex group chosen by
   the Ref-order-no value (routing table — see Discovery).
6. **State/history file** (per MO, local JSON): current Ref order no, first-seen /
   last-seen timestamps per value, last MO status, last publish date, and the cached
   container key. Owns dwell-time history and avoids re-scanning all containers each
   cycle.

---

## Discovery Notes — resolve BEFORE coding the M3 read

Run `discover_mo_header.py` (read-only) on the company laptop via Relay. It targets
MO `7003904788` from the screenshot.

- [ ] **M3 MO-header table** — confirm which of `MWOHED_AP` / `MWOHED` / other exists
      in `PFODS`.
- [ ] **Ref order no columns** — screenshot shows three sub-fields `0 | QM | 2902`.
      Standard M3 reference-order structure is category / number / line
      (`VHRORC` / `VHRORN` / `VHRORL`?). Confirm exact columns + which sub-field
      production actually updates as the process marker.
- [ ] **MO status column** — the "90" in the header (`VHWHST`? `VHWMST`?). Confirm.
- [ ] **MO-number column** — for the per-MO lookup (`VHMFNO`?). Confirm + trimming/padding.
- [ ] **ODBC vs H5 decision** — if Ref order no is NOT in the MO-header table, fall
      back to H5 PMS100 discovery (reuse `m3_h5_client.py` session→generic.do→XML pattern).

Open questions for the user (not code-discoverable):
- [ ] **Webex routing rules** — which Ref-order-no value → which Webex group?
      Need the group list + API (Webex bot token / incoming-webhook URLs).
- [ ] **How does an MO enter the watch list?** All MOs mentioned in open SG SMT PCBA
      container comments? A dedicated field? A schedule file? (Excel→Jira got MOs from
      the daily sheet; this tool needs a live source of "which MOs are active".)
- [ ] **`Day` column semantics** with 15-min polling — keep one row per calendar day
      (upsert, current behaviour) or one row per Ref-order-no change? (Affects table shape.)

## Fields & Data Mapping

### JIRA Fields
| Field | Custom Field ID | Purpose |
|-------|----------------|---------|
| Product Type | customfield_13904 | JQL filter ("SMT PCBA") |
| NPI Location | customfield_13906 | JQL filter ("Singapore") |
| resolution | (system) | Container closed → abandon MO |
| description | (system) | Write target (MO BUILD STATUS table) |

### M3 Tables
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| MWOHED(_AP)? | VHMFNO?, VHRORC/VHRORN/VHRORL?, VHWHST? | **TBD by discovery** — MO#, Ref order no, MO status |

## Edge Cases
- MO number not found in any container comment → log + skip (can't resolve container).
- Ref order no blank / null in M3 → treat as "no change", don't publish blank.
- MO status oscillates around 80 → resume/suspend publishing per gate rule.
- Container closed mid-life → drop MO from watch list.
- Description table for the MO missing → create it (same as Excel→Jira).
- Multiple MOs in one container → each keeps its own `h2. MO BUILD STATUS - {mo}` section.
- Poll overlaps previous run (slow JIRA) → state file is the source of truth; guard re-entrancy.

## Mock Data Needed
- [ ] JIRA search: open SG SMT PCBA containers (JQL above) → `mock_data/search_results.json`
- [ ] JIRA container(s) with comments (MO→container map) → `mock_data/issue_{KEY}.json`
- [ ] M3 MO-header row(s) for sample MOs (post-discovery table/cols) → `mock_data/mo_header_{mo}.json`
- [ ] A synthetic multi-poll sequence (Ref order no changing over time) to test dwell-time math

## Acceptance Criteria
- [ ] Reads current Ref order no + MO status for a given MO (ODBC or H5).
- [ ] Resolves MO → container via comment scan; caches the mapping.
- [ ] Publishes on change; first-run-of-day publishes even without change; no-op otherwise.
- [ ] Status 80/90 → "MO closed" line + dwell-time summary; suspends further writes.
- [ ] Status drop below 80 resumes publishing; container-closed abandons the MO.
- [ ] Webex notification routed to the correct group per Ref-order-no value.
- [ ] Dwell-time (days+hours per stage) matches a hand-computed check.
- [ ] `--mock` runs end-to-end on the VPS with saved data.
