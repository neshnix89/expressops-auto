# mock_data/ — SYNTHETIC fixtures

These files are **hand-made** so the pipeline runs in `--mock` on the VPS
before real data is captured. They are NOT real API responses.

Replace them with real captures on the company laptop:

    python -m tasks.mo_ref_order_monitor.capture

Files:
- `search_*.json`   — JIRA container-universe search result (JQL-named)
- `issue_<KEY>.json` — a container issue (comments carry the MO number,
  plus description + resolution)
- `mo_header_<MO>.json` — one M3 `MWOHED_AP` row for that MO
