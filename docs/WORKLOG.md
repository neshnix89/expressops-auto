# WORKLOG — Tableau KPI Integration Discovery

## Current Task
Discover and connect to Pepperl+Fuchs Tableau Server REST API. Goal: replace the Python KPI pipeline's JIRA/M3/EDM data collection with direct Tableau data extraction.

## What We Know
- Tableau Server 2025.1.8, REST API v3.25
- Base: https://pftableau.pepperl-fuchs.com
- PAT auth confirmed working (add to config.yaml under `tableau` section)
- Token name: Automation
- Secret: <REDACTED — stored in config.yaml on company laptop; ROTATE THIS PAT (was committed in plaintext)>
- Expiry: 2027-05-30
- Target workbook URL: https://pftableau.pepperl-fuchs.com/#/workbooks/3651/views
- Workbook ID from URL: 3651 (may differ from API luid)
- Site contentUrl: "" (default site)
- SSL: self-signed cert, verify=False

## Discovery Steps (via relay to company laptop)
1. POST auth/signin with PAT → get token + site_id
2. GET sites/{site_id}/workbooks?filter=name:eq:... → find workbook luid
3. GET workbook/{luid}/views → list all views
4. GET view/{view_id}/data → try CSV export
5. GET workbook/{luid}/connections → find underlying data source
6. Document everything found

## Auth payload
```json
{"credentials":{"personalAccessTokenName":"Automation","personalAccessTokenSecret":"<REDACTED — stored in config.yaml on company laptop; ROTATE THIS PAT (was committed in plaintext)>","site":{"contentUrl":""}}}
```

## Discovery Results (2026-05-29) — all via relay to company laptop
Run with `scripts/tableau_discovery.py` (deployed via `scripts/sync_from_github.py`).

- **Auth:** PAT signin OK (HTTP 200). site_id `feae689d-1700-48d1-9ec1-27388179a871`
  (default site, contentUrl=""). 257 workbooks visible to the token.
- **URL id → luid:** the `#/workbooks/3651` id is the repository id; it is NOT
  the REST luid and only appears in each workbook's `webpageUrl`. Match a numeric
  URL id by `webpageUrl` ending in `/workbooks/<id>`.
- **Workbook 3651 = "ExpressOps KPIs"** — project "Smart Factory Production",
  luid `2614a7d6-ebde-4aba-93cd-def89d33fb39`.
- **Views (8):**
  - Executive View — `aff50cd8-6554-46d2-af14-5e180d77e517`
  - NPI Build Monitor — `d846a16f-7244-4a6b-a538-201d688b21b8`
  - SMT Build Capacity — `f9edc0cc-cbfe-4d15-9466-2f5dd6d823d7`
  - Work Container (Closed) — `4d7de558-ca0b-4471-b358-e8080c0be3f7`
  - Work Container (Running) — `0899712b-7afe-4439-9f59-c6fb8562de6a`
  - Work Package (Closed) — `f6b90f1c-4c11-492f-8fe1-47970473c0dd`
  - Work Package (Running) — `d29e9f5d-4ebb-4403-8fca-88ea92c6552f`
  - Template_1900x1080 — `34433861-bd26-4fe3-8b7d-aa0ecdb0637a`
- **Data sources (3 published, served by Tableau itself, type `sqlproxy`):**
  - `fact_pm_npi_wc_kpi`        — luid `2c72b33f-dca7-4f80-85b3-41220c5bc355` (Work Container KPIs)
  - `fact_pm_npi_wp_kpi`        — luid `456b9a94-7d61-4dc3-98e7-05555c873f85` (Work Package KPIs)
  - `fact_pm_npi_wc_wp_combined`— luid `eb8a2c04-ca2c-4484-9f7c-1318b61542e7` (combined WC+WP)
- **CSV export:** `GET sites/{site}/views/{viewId}/data` works with `Accept: */*`.
  A specific `Accept: text/csv` returns **HTTP 406**. Executive View returned
  5047 bytes; payload appears UTF-16-encoded (handle encoding in the client).

### Notes for the client implementation
- Best data path is likely the 3 published data sources, not per-view CSV. Next:
  try `GET sites/{site}/datasources/{luid}/data` / download, or the VizQL Data
  Service / Metadata API, to pull KPI rows directly.
- Deploy is relay-only via `scripts/sync_from_github.py` (no git on the laptop).

## End Goal
Python module `core/tableau_client.py` that authenticates and pulls KPI data, replacing JIRA+M3+EDM collection in the KPI pipeline.