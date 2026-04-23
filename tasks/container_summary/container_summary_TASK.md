# Task: container_summary

## Purpose
Generate at-a-glance summaries of all active SMT PCBA Singapore Work Containers
and publish them as a single Confluence dashboard page. Replaces manually scrolling
through 40+ comments per container to understand current state.

## Category
General

## Trigger
Daily (once stable). Initially on-demand (manual run).

## Systems Involved
- [x] JIRA — **read** — fetch active Work Containers, child Work Packages, comments, all custom fields
- [ ] M3 ERP (ODBC) — not needed
- [ ] EDM Oracle — not needed
- [x] Confluence — **write** — publish summary dashboard page
- [x] Anthropic API — **read** — Claude Opus 4.6 for narrative summaries (Phase 2)

## Input
All active SMT PCBA Singapore Work Containers (~40 containers), fetched via the
same nested `relation()` JQL used by mo_trigger_comment:

```
issue in relation("issue in relation("issue in relation('key in (ITPL-769, ITPL-760, ITPL-756, ITPL-750, ITPL-746, ITPL-742, ITPL-1036, ITPL-1027)', 'Project Children', Tasks, Deviations, level4)", "Project Children", 'Clone from Template', level4) and project != 'Issue Template' and status in (Waiting, "In Progress", Backlog)", "Project Parent", Tasks, Deviations, level1) AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" ORDER BY created ASC
```

This JQL is confirmed working in mo_trigger_comment. It spans project keys
USRE, POSX, LCUSAMB, NPIOTHER, SILED2, ACDC, etc.

## CLI Interface

```
python tasks/container_summary/main.py --live              # Phase 1 + Phase 2 (incremental)
python tasks/container_summary/main.py --mock              # VPS dev, reads from mock_data/
python tasks/container_summary/main.py --live --no-llm     # Phase 1 only: Python extraction, no LLM cost
python tasks/container_summary/main.py --live --full-refresh  # Force full re-summarisation of ALL containers
python tasks/container_summary/main.py --live --dry-run    # Build output, print to console, don't publish
python tasks/container_summary/main.py --live --key USRE-1234  # Single container (debug/discovery)
python tasks/container_summary/main.py --live --key USRE-1234 --full-refresh  # Single container, full re-read
```

Flags:
- `--mock` / `--live` (required, default `--mock`)
- `--no-llm` — skip LLM enrichment entirely, Python-only output
- `--full-refresh` — ignore last_run.json cache, re-summarise all containers
- `--dry-run` — build summary but print to console instead of publishing
- `--key KEY` — process single container only

## Architecture Overview

Two layers, always combined in production:

| Layer | What it does | Cost | Runs on |
|-------|-------------|------|---------|
| **Phase 1 — Python snapshot** | Extracts structured fields, WP roll-up, parking log, keyword timeline, flags | $0 | Every container, every run |
| **Phase 2 — Opus narrative** | Reads comment thread, produces Purpose/History/Current/Flags narrative | ~$0.003-0.06/container | Only changed containers (incremental) |

## Logic

### Phase 1 — Structured Python Snapshot (every run, every container)

1. **Fetch containers.** Run the JQL above via `jira_client.search_all()`.
   Fields to request:
   ```
   summary, status, assignee, reporter, created, updated, description,
   comment,
   customfield_13300, customfield_13700,
   customfield_13903, customfield_13904, customfield_13905,
   customfield_13906, customfield_13907, customfield_15009,
   customfield_15400, customfield_15800, customfield_15805
   ```
   Use `expand=renderedFields` for the description (HTML panels/tables).
   Comments: `maxResults=1000` confirmed sufficient (discovery showed 1000 cap,
   largest container has 35 comments).

2. **Fetch child Work Packages** for each container.
   **Do NOT use `jira_client.get_children()`** — it sends bare `relation()` JQL
   that returns HTTP 400 on this JIRA instance. Use `jira_client.search_all()`
   with the correct JQL:
   ```python
   jql = f'issue in relation("{key}", "Project Children", Tasks, Deviations, level1)'
   children = jira.search_all(jql, fields=["summary", "status", "resolution", "assignee"])
   ```
   **Filter out the parent container itself** from results — the relation() JQL
   returns the parent as a "child" of itself. Remove any `wp["key"] == parent_key`.
   Use ThreadPoolExecutor (10 workers) for parallel fetch.

3. **For each container, extract structured data** (pure Python in `logic.py`):

   a. **Identity block:**
      - Key, Summary
      - Order Type (`customfield_13905`) — dict, use `.get("value")`
      - Product Type (`customfield_13904`) — dict
      - NPI Location (`customfield_13906`) — dict
      - Request Type (`customfield_13903`) — dict
      - Project Status (`customfield_13700`) — None on all tested containers
      - WC NPI Status (`customfield_15400`) — dict, e.g. `"Red"`
      - Status Light (`customfield_15009`) — None on all tested containers
      - PTxx Document (`customfield_13907`) — plain string
      - Reporter, Assignee
      - Created date, Last Updated date

   b. **WP status roll-up** (one line):
      Count total WPs, count by status category (Done, In Progress, Waiting,
      Backlog). Format: `"5/8 done | SMT Build: In Progress | Routing: Waiting"`
      Highlight: SMT Build status, any WP in "Blocked" or parked state.

   c. **Parking log** (`customfield_15800`):
      Format is plain string: `"Start:2026-01-15 10:30:00;End:2026-01-17 14:00:00;Start:..."`
      Parse into list of `{start, end}` pairs. If last entry has Start without
      End, container is currently parked. Calculate total parked working days.

   d. **Keyword timeline** from comments:
      Scan ALL comments for signal keywords and build an event log:
      - Structure/routing: `"created"`, `"released"`, `"completed"`, `"verified"`
      - Material/delivery: `"ETA"`, `"arrived"`, `"booked-in"`, `"shortage"`, `"available"`
      - Build: `"MO"`, `"stencil"`, `"PnP program"`, `"build"`
      - Shipping: `"TO:"`, `"shipped"`, `"handed-over"`
      - Blockers: `"parked"`, `"flag"`, `"delayed"`, `"issue"`, `"problem"`, `"NG"`
      - Test: `"AOI"`, `"test"`, `"908"`, `"False Call"`
      Extract: date + author + matched keyword + first 80 chars of context.
      Keep max 8 most recent timeline entries per container.

   e. **Comment stats:**
      - Total comment count
      - Last comment: author, date, first 150 chars
      - Count of auto-generated comments (containing `#Ref:` markers)
      - Count of comments with attachments (`!filename.ext|` or `[^filename]` patterns)
      - Flag if any non-English text detected (CJK unicode ranges \u4e00-\u9fff, \u3040-\u30ff)

   f. **Age & staleness:**
      - Container age in working days (created → today, using SG holidays)
      - Days since last update
      - Days since last human comment (excluding auto-generated)
      - Flag if stale (>5 working days since last human activity)

   g. **Flags** (anything noteworthy):
      - `"PARKED"` — currently parked (red badge)
      - `"STALE"` — no human activity in >5 working days (red badge)
      - `"NO_ASSIGNEE"` — in-progress WP has no assignee (red badge)
      - `"NON_EN"` — non-English comments present (yellow badge)
      - `"HIGH_COMMENTS"` — >30 comments (yellow badge)
      - `"ATTACHMENTS"` — attachments referenced in comments (green badge)

### Phase 2 — Opus 4.6 Narrative Summary (incremental, daily)

#### Cost Optimization: Three Layers

**Layer 1 — Skip unchanged containers.**
Maintain `tasks/container_summary/last_run.json`:
```json
{
  "NPIOTHER-3902": {
    "jira_updated": "2026-04-13T03:55:00",
    "last_comment_count": 24,
    "narrative": "DS – Dev sample for LT66 sensor. Structure and E5 released Feb 26...",
    "generated_at": "2026-04-14T09:30:00"
  }
}
```
On each run, compare the container's `fields.updated` timestamp and comment count
against the cached values. If both match → reuse cached narrative, skip LLM call.
Typical day: ~12 of 40 containers have new activity → 70% calls saved.

**Layer 2 — Incremental summarisation.**
For changed containers, do NOT send the full comment history. Send:
- Previous narrative from cache (~200 tokens)
- Only NEW comments since last run (~500-1k tokens)
- Instruction: "Update this summary incorporating the new activity below"

This drops input from ~10k to ~2k tokens per changed container.
First-ever run for a container (no cache) sends full comment history.

**Layer 3 — Prompt caching (Anthropic API).**
System prompt is identical across all 40 containers.
Mark with `cache_control: {"type": "ephemeral"}` — billed at $0.50/M (90% off
the $5/M input rate) after the first call in a batch.

#### LLM Call Details

- **Model:** `claude-opus-4-6`
- **API key:** Existing `anthropic.api_key` in config.yaml
- **SDK:** Existing `anthropic` Python package
- **No extended thinking** — summarisation doesn't need it. Standard completion
  avoids expensive thinking output tokens.

**System prompt (cached, ~400 tokens):**
```
You are summarising JIRA Work Container activity for an NPI (New Product
Introduction) team managing SMT PCBA production at Pepperl+Fuchs Singapore.

The audience is the operations coordinator who needs to understand each
container's status at a glance. Write in English regardless of comment language.
Translate any non-English content (German, Chinese, Czech) naturally.

Output exactly four sections in this format:

**Purpose:** [1-2 sentences: what is being built, order type, key part numbers]
**History:** [3-5 bullets: key milestones, decisions, blockers encountered]
**Current:** [2-3 bullets: what's happening now, who is responsible, any ETA]
**Flags:** [0-3 bullets: anything unresolved, risky, or worth escalating. Omit section if nothing to flag.]

Rules:
- Reference people by name (as they appear in comments).
- Include specific part numbers, TO numbers, MO numbers when mentioned.
- Do not invent information. If something is unclear from the comments, say so.
- Keep the total summary under 200 words.
```

**User message — full refresh:**
```
Container: {key} — {summary}
Order Type: {order_type} | Status: {status}
WP Roll-up: {wp_summary_line}

Comments (oldest first):
[1] {author} @ {date}: {body}
[2] {author} @ {date}: {body}
...
```

**User message — incremental update:**
```
Container: {key} — {summary}

Previous summary:
{cached_narrative}

New activity since {last_run_date}:
[25] {author} @ {date}: {body}
[26] {author} @ {date}: {body}

Update the summary to incorporate the new activity above.
```

#### Cost Estimate (optimized)

| Scenario | Containers | Input tokens | Output tokens | Cost |
|----------|-----------|-------------|---------------|------|
| Typical day (12 changed, incremental) | 12 | ~24k | ~6k | ~$0.27 |
| Full refresh (weekly, all 40) | 40 | ~400k | ~20k | ~$2.50 |
| Single deep-dive (`--key`) | 1 | ~10k | ~500 | ~$0.06 |

**Estimated monthly: ~$8-9** (22 incremental days + 4 weekly full refreshes)

### Output Assembly

4. **Build Confluence HTML.** Single table, one row per container.
   Columns: Key (linked), Summary, Order Type, Status, WP Roll-up,
   Narrative (expandable), Last Activity, Age (wd), Flags.
   Sort by: created ASC (matches JQL).
   Narrative column: use `ac:structured-macro` expand macro so the
   dashboard stays compact but narrative is one click away.
   Use `ac:structured-macro` status badges for flag severity (red/yellow/green).

5. **Publish to Confluence.** Read existing page first (preserve version),
   then update with new HTML. Page ID from `config.pages["container_summary"]`.

6. **Save last_run.json** after successful publish. Only update the cache
   AFTER Confluence publish succeeds — if publish fails, next run re-processes.

7. **Console output.** Always print a compact summary table:
```
Container     Summary                          Status        WPs        Age  Flags
USRE-1234     PCB Assembly Rev B               In Progress   5/8 done   12d
POSX-567      Sensor Module QS                 Waiting       2/7 done   45d  STALE, PARKED
NPIOTHER-890  Controller Board DMR             In Progress   7/8 done    8d  NON_EN
```

## Fields & Data Mapping

### JIRA Fields (confirmed in CLAUDE.md + discovery)
| Field | Custom Field ID | API Type | Access Pattern |
|-------|----------------|----------|---------------|
| Order Type | `customfield_13905` | `dict` | `.get("value")` → `"DS – Development sample"` |
| Product Type | `customfield_13904` | `dict` | `.get("value")` → `"SMT PCBA"` |
| NPI Location | `customfield_13906` | `dict` | `.get("value")` → `"Singapore"` |
| Request Type | `customfield_13903` | `dict` | `.get("value")` → `"NPI Request"` |
| NPI WC Status | `customfield_15400` | `dict` | `.get("value")` → `"Red"` / `"Green"` |
| Issue Parked Log | `customfield_15800` | `str` or `None` | Raw `"Start:...;End:...;"` |
| PTxx Document | `customfield_13907` | `str` or `None` | Plain string |
| Project Status | `customfield_13700` | `None` | Empty on all tested containers |
| Status Light | `customfield_15009` | `None` | Empty on all tested containers |
| EDM Doc Number | `customfield_13300` | `None` | Empty on tested containers |
| M3 Article Number | `customfield_13502` | `None` | Empty (confirmed CLAUDE.md) |
| Component Part Number | `customfield_15805` | `None` | Empty (confirmed CLAUDE.md) |

### JIRA Comment Pagination
**Confirmed: NOT needed.** JIRA returns `maxResults=1000` for inline comments.
Largest tested container has 35 comments. No separate pagination call required.

### Confluence
| Item | Value | Purpose |
|------|-------|---------|
| Page ID | **TBD** | Create manually, add to `config.yaml` as `pages.container_summary` |
| Space | `EUDEMHTM0021` | Standard ExpressOPS space |

## Discovery Notes (Resolved)

- [x] **Comment pagination** — `maxResults=1000`, all returned inline. No pagination needed.
- [x] **Custom field formats** — Five dict-type (use `.get("value")`), two plain strings, five None.
- [x] **renderedFields** — Available for comments. Using raw `body` for token efficiency.
- [x] **LLM model choice** — Opus 4.6 with 3-layer optimization. ~$8-9/month.
- [x] **get_children() is broken** — bare `relation()` JQL returns HTTP 400. Must use
      `search_all()` with full syntax: `relation("KEY", "Project Children", Tasks, Deviations, level1)`.
      This also affects `core/jira_client.py` — file a fix separately.
- [x] **Parent self-reference in children** — relation() JQL returns the parent container
      as a "child" of itself. Filter out `wp["key"] == parent_key` before building WP roll-up.

## Remaining Discovery

- [ ] **Confluence page creation.** Create target page, record ID in config.yaml.
- [ ] **Keyword timeline tuning.** Review signal words after first real run.
- [ ] **Opus output quality.** Review narratives after first `--full-refresh` for accuracy.

## Edge Cases
- Container with 0 comments — show "No activity" in timeline, skip LLM
- Container first seen (not in last_run.json) — full LLM summarisation
- Container removed from JQL (closed) — remove from last_run.json
- Parking log with malformed timestamps — log warning, skip entry
- Currently parked (Start without End) — flag as PARKED
- Custom field returns None vs empty string vs empty dict — defensive `.get()` chains
- No child WPs — show "No WPs" in roll-up
- JQL returns 0 results — publish empty table with timestamp
- Confluence page doesn't exist — raise FriendlyError with setup instructions
- LLM call fails for one container — log error, use Phase 1 only, continue
- last_run.json missing/corrupted — treat as first run, full refresh
- `--full-refresh` costs ~$2.50 — log cost estimate before proceeding

## Mock Data Needed
- [x] Full JIRA issue JSON for NPIOTHER-3902 (24 comments, parking log)
- [x] Full JIRA issue JSON for LCUSAMB-1755 (35 comments, complex thread)
- [x] Full JIRA issue JSON for NPIOTHER-4085 (12 comments, simple)
- [x] Child WP list for NPIOTHER-3902 (9 results, 8 WPs + parent self-ref, all Done)
- [x] Child WP list for LCUSAMB-1755 (9 results, 8 WPs + parent self-ref, all Done)
- [x] Child WP list for NPIOTHER-4085 (9 results, 8 WPs + parent self-ref, TE Won't Do)
- [ ] JQL search result for mock search_containers.json (capture.py on laptop)

## Acceptance Criteria
- [ ] Fetches all active SMT PCBA Singapore containers via nested JQL
- [ ] Extracts custom fields with correct type handling (dict vs str vs None)
- [ ] Builds WP roll-up (X/Y done, highlights SMT Build and blocked WPs)
- [ ] Parses parking log (including currently-parked detection)
- [ ] Builds keyword timeline from comments (max 8 entries)
- [ ] Identifies stale containers (>5 wd since last human comment)
- [ ] Detects non-English text and attachment references
- [ ] Layer 1: Skips unchanged containers (compares updated + comment count)
- [ ] Layer 2: Incremental update sends only new comments + cached narrative
- [ ] Layer 3: System prompt cached across batch
- [ ] last_run.json saved only after successful Confluence publish
- [ ] `--full-refresh` forces complete re-summarisation
- [ ] `--no-llm` skips all LLM calls
- [ ] Publishes HTML table with expandable narrative and status badges
- [ ] `--dry-run` prints to console without publishing
- [ ] `--key` mode works for single container
- [ ] `--mock` / `--live` modes both functional
- [ ] Individual container errors don't crash the batch

## Future Phases
- **Phase 3:** Tampermonkey overlay injects summary into JIRA container view
- **Phase 4:** Hybrid auto-escalation (Python detects complexity → auto-calls LLM)
