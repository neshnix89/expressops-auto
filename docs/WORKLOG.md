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

## End Goal
Python module `core/tableau_client.py` that authenticates and pulls KPI data, replacing JIRA+M3+EDM collection in the KPI pipeline.