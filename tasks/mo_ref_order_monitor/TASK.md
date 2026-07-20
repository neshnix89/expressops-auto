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

## Discovery Notes — M3 CONFIRMED (discover_mo_header.py, MO 7003904788, 2026-07)

**Decision: ODBC path confirmed. No H5 scraping needed.** Table
`PFODS.MWOHED_AP` (123 cols; `MWOHED` also exists with 131) holds everything.

Confirmed columns (VH-prefixed, verified against the P1 screenshot):

| Column | Meaning | Sample |
|--------|---------|--------|
| `VHMFNO` | MO number (lookup key) | `7003904788` |
| `VHPRNO` | Product number | `70209808` |
| `VHWHST` | **MO status — the 80/90 gate** | `90` |
| `VHWHHS` | Highest status ever reached (→ positive re-open detection) | `90` |
| `VHWMST` | Material status (NOT the gate) | `99` |
| `VHRORC` | Ref order **category** (box 1) | `0` |
| `VHRORN` | Ref order **number** (box 2 — the highlighted process marker) | `QM` |
| `VHRORL` | Ref order **line** (box 3) | `2902` |
| `VHORTY` | Order type | `SPI` |
| `VHFACI` | Facility | `MF1` |
| `VHRESP` | Responsible | `MP-3459` |
| `VHTXT2` | Order text (free) | `Thinesh PR NEXPERIA … (#021357)` |
| `VHLMDT` / `VHCHNO` / `VHCHID` | Last-modified date / change# / changed-by | `2026-07-17` / `9` / `PECKCHOO` |

Key consequences:
- **No per-field change history in M3.** `VHLMDT`/`VHCHNO` only flag that the header
  changed at all — not that Ref order no specifically advanced. → The poller MUST build
  its own history (record value+timestamp, close prior stage on change). Dwell time is
  ours to compute.
- **Re-open detection:** `VHWHST` is the live status; `VHWHHS` holds the highest ever.
  If `VHWHST` drops below 80 while `VHWHHS` >= 80, the MO was re-opened → resume publishing.

Confirmed with user:
- [x] **Tracked process marker = `VHRORN` only** (the "QM" text). `VHRORL`/`VHRORC`
      are ignored. `VHRORN` is the change-detection value, the dwell "stage" identity,
      and the Webex routing key.
- [x] **Table shape = per-day row** (same as Excel→Jira `MO BUILD STATUS` format):
      one row per calendar day, overwritten intraday with the latest stage; first run
      of each day writes a heartbeat row even if unchanged. Fine granularity lives in
      Webex-on-change + the dwell summary, not in the table shape.

Still open (minor):
- [ ] **PIC column source** — Excel supplied PIC per row; M3 has no direct equivalent.
      Default to `VHRESP` (responsible, e.g. "MP-3459") for now; confirm/replace later.

Decisions made:
- [x] **MO watch list = scan container comments** (option 1a). MOs are discovered by
      scanning open SG SMT PCBA container comments for MO numbers (reuses the existing
      MO→container bridge; no new input source). An MO is watched continuously — even
      after status hits 80/90 — and only dropped when its JIRA container is closed.
- [x] **Webex = bot token** (single credential, route to any space by `roomId`). Run
      `discover_webex_rooms.py` to enumerate the bot's spaces → build the value→roomId map.

Still open:
- [ ] **Webex routing rules** — which Ref-order-no value → which `roomId`? Needs the bot
      token + the room list from discovery + the value→group mapping from the user.
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

### M3 Tables (CONFIRMED)
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `PFODS.MWOHED_AP` | `VHMFNO` (MO#), `VHWHST` (status), `VHWHHS` (highest status), `VHRORC`/`VHRORN`/`VHRORL` (Ref order no), `VHORTY`, `VHFACI`, `VHTXT2` | MO header — poll per MO for Ref order no + status |

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
