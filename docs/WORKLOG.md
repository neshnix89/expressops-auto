# WORKLOG — Current Task

> Task: to_status_check
> Phase: Discovery + Initial Build
> Location: tasks/to_status_check/

---

## What To Do

1. Read `tasks/to_status_check/TASK.md` for the full spec.
2. **Phase A — JIRA side only (build now):**
   - Create `main.py` and `logic.py` in `tasks/to_status_check/`.
   - Pull active Work Containers from JIRA.
   - Extract the TO number from each container (field TBD — see Discovery in TASK.md).
   - Output a table of containers with their TO numbers.
   - Support `--mock` mode reading from `mock_data/`.
3. **Phase B — M3 side (build after discovery):**
   - Look up each TO number in M3 to check status.
   - Flag containers where TO status = 90.
   - Add `--auto-close` flag for future use (don't implement the close yet).
4. Create a `capture.py` in the task folder for `ops capture to_status_check`.
5. Commit and push to GitHub when done.

## What NOT To Do

- Do NOT implement the actual JIRA close/transition. That's a write operation for later.
- Do NOT guess M3 table or column names. Mark unknowns in TASK.md Discovery section.
- Do NOT modify any core/ modules unless there's a clear bug.

## Notes from Planning

- The TO number field in JIRA is unknown. It might be in the summary, description, a custom field, or a linked field. Nesh needs to check a sample container manually and report back.
- The M3 table for TO status is unknown. Nesh will use `M3Client.explore_table()` on the company laptop to investigate.
- Start with JIRA extraction only. Get that working and producing output. M3 integration comes after discovery.
- This is the first task in the framework — it should serve as the template for all future tasks.
