# Claude Code Prompt — container_summary task

## Context
Read these files FIRST before writing any code:
- `CLAUDE.md` (repo bible — architecture, coding standards, system access details)
- `tasks/container_summary/TASK.md` (the full spec for this task)
- `core/jira_client.py` (JIRA REST client — `search_all`, `parse_timestamp`.
  NOTE: `get_children()` is broken on this JIRA instance — do NOT use it)
- `core/confluence.py` (Confluence client — `get_page`, `update_page`)
- `core/config_loader.py` (config access — `Config` class, `load_config()`)
- `core/errors.py` (error handling — `FriendlyError`, `handle_friendly`)
- `core/logger.py` (logging — `get_logger`)
- `tasks/mo_trigger_comment/logic.py` (reference for: custom field extraction, WP lookup,
  working-day arithmetic with SG holidays, comment parsing patterns)
- `tasks/mo_trigger_comment/main.py` (reference for: CLI arg parsing, mock/live setup,
  main loop structure, ThreadPoolExecutor pattern)

## What to build

Create `tasks/container_summary/` with this structure:
```
tasks/container_summary/
├── TASK.md          ← already exists (the spec)
├── __init__.py      ← empty
├── main.py          ← entry point: CLI, fetch, orchestrate, publish
├── logic.py         ← pure business logic: extraction, roll-ups, HTML assembly
├── llm.py           ← Opus 4.6 integration: prompt assembly, caching, incremental updates
├── cache.py         ← last_run.json read/write/compare logic
├── discover.py      ← already exists, do NOT regenerate
├── capture.py       ← mock data capture script
└── mock_data/       ← directory for saved JSON (populated by capture.py / discover.py)
```

## File-by-file instructions

### `logic.py` — Pure business logic, NO I/O

No `requests`, no file I/O, no config access, no `anthropic` SDK.
Must be testable with mock data dicts.

**Functions to implement:**

1. `extract_identity(issue: dict) -> dict`
   Extract: key, summary, status name, assignee displayName, reporter displayName,
   created (via `JiraClient.parse_timestamp`), updated (same).
   Custom fields — handle two patterns:
   - Dict fields (13903, 13904, 13905, 13906, 15400): `(val or {}).get("value", "")`
   - String fields (13907, 15800): `str(val or "").strip()`
   - Known-empty fields (13300, 13502, 13700, 15009, 15805): extract but expect None
   Return a flat dict with readable keys like `order_type`, `product_type`, etc.

2. `build_wp_rollup(children: list[dict]) -> dict`
   **Input is pre-filtered** — parent container has already been removed by caller.
   Count: total, done (resolution in {"Done", "Acknowledged", "Won't Do"}),
   in_progress (status "In Progress"), waiting, backlog, other.
   Find SMT Build WP (case-insensitive match on "smt build") — extract status.
   Find any WP with status containing "blocked" (case-insensitive).
   Return: `{total, done, in_progress, waiting, summary_line, smt_build_status, blocked_wps}`.
   `summary_line` format: `"5/8 done | SMT Build: In Progress | Routing: Waiting"`.
   Use the same `_wp_status_name()` / `_wp_resolution_name()` patterns as
   mo_trigger_comment (reimplement locally — don't import to avoid circular deps).

3. `parse_parking_log(raw: str | None) -> dict`
   Input: `customfield_15800` — format: `"Start:2026-01-15 10:30:00;End:2026-01-17 14:00:00;Start:..."`
   Parse into list of `{start: datetime, end: datetime | None}` pairs.
   If last entry has Start but no End → currently parked.
   Calculate total parked working days using SG holidays.
   Return: `{entries: [...], currently_parked: bool, total_parked_days: int}`.

4. `build_keyword_timeline(comments: list[dict]) -> list[dict]`
   Scan all comment bodies for signal keywords:
   - Structure: "created", "released", "completed", "verified"
   - Material: "ETA", "arrived", "booked-in", "shortage", "available"
   - Build: "MO", "stencil", "PnP program", "build"
   - Shipping: "TO:", "shipped", "handed-over"
   - Blockers: "parked", "flag", "delayed", "issue", "problem", "NG"
   - Test: "AOI", "test", "908", "False Call"
   For each match: `{date, author, keyword, context: first_80_chars}`.
   Return max 8 most recent entries, sorted by date descending.
   Skip auto-generated comments (body contains `#Ref:`).

5. `analyse_comments(comments: list[dict]) -> dict`
   Total count, auto-generated count (#Ref:), attachment count, has_non_english,
   last_human_comment (excluding auto-generated), last_comment_date.
   Non-English: CJK \u4e00-\u9fff, \u3040-\u30ff; German ä/ö/ü/ß.
   Attachments: `!` followed by filename pattern or `[^` syntax.
   Return structured dict.

6. `calculate_staleness(created: datetime, updated: datetime, last_human_date: datetime | None, today: date) -> dict`
   Age in working days, days since update, days since human comment.
   Stale if >5 working days since last human activity.
   Use SG holiday set (copy from mo_trigger_comment or extract to shared utility).
   Return: `{age_wd, days_since_update, days_since_human, is_stale}`.

7. `build_flags(identity, wp_rollup, parking, comments_analysis, staleness) -> list[str]`
   Collect flags as short uppercase strings: PARKED, STALE, NO_ASSIGNEE, NON_EN,
   HIGH_COMMENTS (>30), ATTACHMENTS. Return sorted list.

8. `summarise_container(issue: dict, children: list[dict], today: date) -> dict`
   Orchestrator: calls 1-7, returns single dict with all summary data.

9. `build_confluence_html(summaries: list[dict]) -> str`
   Build full Confluence HTML table. One row per container.
   - Key: `<a href="/browse/{key}">{key}</a>`
   - Narrative: use Confluence expand macro:
     ```html
     <ac:structured-macro ac:name="expand">
       <ac:parameter ac:name="title">View summary</ac:parameter>
       <ac:rich-text-body>{narrative_html}</ac:rich-text-body>
     </ac:structured-macro>
     ```
   - Flags: Confluence status badge macros:
     Red for PARKED/STALE/NO_ASSIGNEE, Yellow for NON_EN/HIGH_COMMENTS,
     Green for ATTACHMENTS.
   - Table classes: `confluenceTable`, `confluenceTh`, `confluenceTd`
   - Wrap in panel with title + generation timestamp
   - Empty summaries → "No active containers found" message

### `cache.py` — last_run.json management

Simple module for reading, writing, and comparing the incremental cache.

1. `load_cache(cache_path: Path) -> dict`
   Load last_run.json. Return empty dict if missing or corrupted (log warning).

2. `save_cache(cache_path: Path, cache: dict) -> None`
   Write last_run.json atomically (write to .tmp, then rename — prevents
   corruption if the process crashes mid-write).

3. `needs_update(cache_entry: dict | None, issue: dict) -> bool`
   Compare `fields.updated` timestamp and comment count against cached values.
   Return True if either changed or if no cache entry exists.

4. `update_cache_entry(key: str, issue: dict, narrative: str) -> dict`
   Build a new cache entry dict for a container after processing.

### `llm.py` — Opus 4.6 integration

Handles all Anthropic API interaction. Isolated so main.py stays clean.

1. `create_client(config: Config) -> anthropic.Anthropic`
   Initialize the Anthropic client with `config.anthropic_api_key`.

2. `SYSTEM_PROMPT: str`
   The static system prompt from TASK.md (the one that gets cached).

3. `build_full_payload(issue: dict, identity: dict, wp_rollup: dict) -> str`
   Build the user message for a full-refresh call. Include:
   container key, summary, order type, status, WP roll-up line,
   then ALL comments (oldest first) as `[N] author @ date: body`.
   Strip wiki markup noise (color tags, panel macros) but keep content.

4. `build_incremental_payload(issue: dict, identity: dict, cached_narrative: str, new_comments: list[dict]) -> str`
   Build the user message for an incremental update.
   Include: container key, summary, previous narrative, then only new comments.

5. `get_new_comments(comments: list[dict], cached_comment_count: int) -> list[dict]`
   Return comments beyond the cached count. Simple: comments[cached_count:].

6. `call_opus(client: anthropic.Anthropic, user_message: str, logger) -> str`
   Make the API call with:
   - model: `claude-opus-4-6`
   - system prompt with `cache_control: {"type": "ephemeral"}`
   - max_tokens: 600 (200 words ≈ ~300 tokens, add buffer)
   - NO extended thinking
   Return the text response.
   Wrap in try/except — on failure, log error and return empty string
   (caller falls back to Phase 1 only).

7. `estimate_batch_cost(containers_to_process: int, is_full_refresh: bool) -> float`
   Rough cost estimate. Log before processing so operator sees it.

### `main.py` — Entry point with I/O

Follow mo_trigger_comment's main.py as the structural template.

1. **CLI argument parsing** (argparse):
   - `--mock` / `--live` (mutually exclusive, required)
   - `--no-llm` (optional flag)
   - `--full-refresh` (optional flag)
   - `--dry-run` (optional flag)
   - `--key KEY` (optional, single container mode)

2. **Initialisation:**
   - `load_config(mode_override=args.mode)`
   - `get_logger("container_summary", config.log_dir, config.log_level)`
   - `JiraClient(config, mock_data_dir=MOCK_DIR)`
   - `ConfluenceClient(config, mock_data_dir=MOCK_DIR)`
   - `MOCK_DIR = Path(__file__).parent / "mock_data"`
   - `CACHE_PATH = Path(__file__).parent / "last_run.json"`

3. **Fetch containers:**
   - If `--key`: fetch single issue, wrap in list
   - Otherwise: `jira.search_all()` with full JQL and field list
   - **Mock mode override:** In mock mode, load directly from
     `mock_data/search_containers.json` rather than going through
     `jira.search_all()` (the JQL is too long for the mock filename convention).
   - Log: `"Fetched {n} containers"`

4. **Fetch children per container:**
   - **Do NOT use `jira.get_children()`** — it's broken (bare `relation()` JQL
     returns HTTP 400 on this JIRA instance).
   - Use `jira.search_all()` with the correct JQL:
     ```python
     jql = f'issue in relation("{key}", "Project Children", Tasks, Deviations, level1)'
     children = jira.search_all(jql, fields=["summary", "status", "resolution", "assignee"])
     # Filter out parent self-reference
     children = [wp for wp in children if wp["key"] != key]
     ```
   - ThreadPoolExecutor, 10 workers
   - try/except per container — log error, return empty list on failure
   - Mock mode: load from `mock_data/children_{key}.json` (also filter parent)

5. **Phase 1 — Python extraction:**
   - Call `logic.summarise_container(issue, children, today)` for each
   - Collect into summaries list

6. **Phase 2 — Opus narratives (unless `--no-llm`):**
   - Load cache: `cache.load_cache(CACHE_PATH)`
   - For each container:
     - If `--full-refresh` OR `cache.needs_update(...)`:
       - If full-refresh or no cache entry: `llm.build_full_payload(...)`
       - Else: `llm.build_incremental_payload(...)` with new comments
       - Call `llm.call_opus(client, payload, logger)`
       - Store narrative in summary dict
     - Else: reuse cached narrative
   - Log: `"LLM: {n} containers processed, {m} skipped (unchanged)"`
   - Log estimated cost

7. **Build HTML:** `logic.build_confluence_html(summaries)`

8. **Output:**
   - Always print console table
   - If `--dry-run`: print HTML, don't publish
   - Otherwise: publish to Confluence via `config.pages["container_summary"]`
     - If page ID not in config: raise FriendlyError with setup instructions
   - **After successful publish**: `cache.save_cache(CACHE_PATH, updated_cache)`
     - Only save cache after publish succeeds

9. **Error handling:**
   - Wrap `main()` in try/except FriendlyError → `handle_friendly(exc)`
   - Individual container failures: log, skip, continue

### `capture.py` — Mock data capture

Run on company laptop. Saves API responses for VPS testing.

- Fetch all containers via the full JQL, save as `search_containers.json`
- For first 5 containers, fetch children → `children_{key}.json`
- For first 3 containers, fetch full issue → `issue_{key}.json`
- Handle errors per container (don't crash on one failure)
- Include the full JQL string and field list from TASK.md

### `discover.py`
Already completed and ran on company laptop. Do NOT create or regenerate this file.
Discovery results are documented in TASK.md under "Discovery Notes (Resolved)".

## Critical implementation rules

1. **`logic.py` has ZERO imports from `core/`** except `JiraClient.parse_timestamp`
   (static method, no I/O). Copy the SG holiday set locally or create a shared
   `core/dates.py` utility (preferred — mo_trigger_comment can import it too).

2. **All custom field access uses defensive chains:**
   ```python
   val = (fields.get("customfield_13905") or {})
   order_type = val.get("value", "") if isinstance(val, dict) else str(val or "").strip()
   ```

3. **Case-insensitive WP name matching** everywhere. `.lower()` comparisons.

4. **Timestamps:** Always use `JiraClient.parse_timestamp()` or equivalent.

5. **No hardcoded page IDs.** Use `config.pages["container_summary"]`.

6. **Mock mode for search:** Load from known filename `search_containers.json`,
   not through `jira.search_all()` (the nested JQL produces an unusable mock filename).

7. **Confluence HTML must be valid storage format.** Use `confluenceTable` /
   `confluenceTh` / `confluenceTd` classes. Test expand macros render correctly.

8. **ThreadPoolExecutor:** 10 workers max for children fetch. Use `search_all()`
   with correct relation JQL (not `get_children()`). try/except per fetch.
   Filter parent self-reference from results.

9. **Cache atomicity:** Write last_run.json to `.tmp` then rename. Never save
   cache before Confluence publish succeeds.

10. **Anthropic API call structure:**
    ```python
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=600,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=[{"role": "user", "content": user_message}]
    )
    narrative = response.content[0].text
    ```

11. **Cost logging:** Log token usage after each Opus call:
    `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
    `cache_read_input_tokens`. Sum and log total cost at end of batch.

12. **Git:** Branch `task/container-summary`. Commits: `[container-summary] description`.
