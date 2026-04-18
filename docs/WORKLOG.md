# WORKLOG — Current Task

> Task: to_status_check
> Phase: Build Phase A (JIRA side)
> Location: tasks/to_status_check/

---

## What To Do

1. Read `tasks/to_status_check/TASK.md` for the full spec.
2. **Build Phase A — JIRA extraction:**
   - Create `main.py` and `logic.py` in `tasks/to_status_check/`.
   - Pull active Work Containers from JIRA (project EXPRESSOPS, status != Closed).
   - For each container, fetch its comments via REST API: GET /rest/api/2/issue/{key}?expand=renderedFields&fields=comment
   - Extract TO number from comments using regex: `TO:\s*(\d+)`
   - The TO number is added as a comment by the team, format "TO: 147715" (pure numeric, typically 6 digits).
   - If multiple TO comments exist, use the LATEST one.
   - Output a table of containers with their TO numbers (or "No TO" if none found).
   - Support `--mock` mode reading from `mock_data/`.
3. **Phase B — M3 lookup: NOT YET. Discovery still needed for which M3 table holds TO status.**
4. Create a `capture.py` in the task folder for `ops capture to_status_check` that saves:
   - JIRA search results (all active containers)
   - JIRA comments for 5-10 sample containers
5. Commit and push to GitHub when done.

## What NOT To Do

- Do NOT build M3 integration yet — table/column mapping is unknown.
- Do NOT implement JIRA close/transition — that's a future write operation.
- Do NOT modify core/ modules unless there's a bug.
- Do NOT guess anything. If uncertain, add to Discovery in TASK.md.

## Key Discovery (Confirmed)

- **TO number location:** JIRA comment on the Work Container
- **TO number format:** `TO: XXXXXX` — pure numeric, typically 6 digits
- **Extraction regex:** `TO:\s*(\d+)`
- **Who adds it:** Team member (manual comment)

## Still Unknown

- Which M3 table holds TO/Transfer Order data and status
- What status 90 means exactly
- TO number column name in M3