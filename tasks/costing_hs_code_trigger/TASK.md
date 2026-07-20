# Task: costing_hs_code_trigger

## Purpose
When a Work Container is ready, post ONE JIRA comment tagging the people who
must update **Costing** (2 people) and the **HS Code** (1 person), asking them
to reply "Done". Then, on a recurring schedule, check back: if a tagged person
hasn't replied Done within 2 working days, re-tag whoever is still outstanding
with a reminder — repeating every 2 working days until everyone is done.

This is a sibling of `mo_trigger_comment`: same readiness gate concept and the
same JIRA `[~username]` mention mechanism, plus a stateful reminder loop.

## Category
General — scheduled (Windows Task Scheduler on company laptop, e.g. daily).

## Trigger
Scheduled/recurring. Each run scans in-scope containers and, per container,
either posts the initial trigger comment, posts a reminder, or does nothing.

## Systems Involved
- [x] JIRA — read — WP resolutions, Order Type, container comments
- [x] JIRA — write — post trigger + reminder comments (live only)
- [ ] M3 / EDM / Confluence — not used

---

## Scope (JQL — same as mo_trigger_comment)
```
issuetype = "Work Container"
  AND "Product Type" = "SMT PCBA"
  AND "NPI Location" = "Singapore"
  AND resolution is EMPTY
  ORDER BY created ASC
```
Child WP fetch: `issue in relation("{wc_key}", "Project Children", level1)`
WP fields: `key, summary, status, resolution, assignee`

---

## Trigger Gate (when do we post the FIRST comment?)

Two branches, decided by Order Type (`customfield_13905`):

| Container type | Condition to trigger |
|----------------|----------------------|
| **DMR** (Order Type = "DMR - Direct manufacturing release") | Immediately — the moment it appears in the filter. DMR containers do **not** carry the prerequisite WPs, so there is nothing to wait for. |
| **All others** | All five prerequisite WPs resolved as **Done** or **Won't Do**: Material, PCB, Routing - TechnPrep, PE - TechnPrep, TE - TechnPrep. |

WP name matching is case-insensitive. Accepted resolutions are configurable
(`ready_resolutions`, default `["Done", "Acknowledged", "Won't Do"]` — matches
`mo_trigger_comment`, where "Won't Do"/"Acknowledged" mark a step legitimately
skipped or signed off).

A container is considered "already triggered" when any existing comment
contains the trigger marker — that guards against double-posting.

---

## Tracking Model — two independent tracks

- **Costing** track = the 2 costing people.
- **HS Code** track = the 1 HS Code person.

Completion is tracked **per person**: a person is *done* when they have posted
a comment, *after* our trigger comment, authored by their JIRA username, whose
body contains a configured done-keyword (default `done`, `updated`,
`completed`) and none of the negation guards (default `not done`, `no update`,
`pending`). Reminders re-tag **only the people still outstanding**. When all
three are done, the container needs no further action.

---

## Reminder Timing — working days

- The trigger comment (and each reminder) carries a marker; JIRA stamps each
  with a `created` timestamp.
- A reminder is **due** when `>= reminder_interval_working_days` (default 2)
  working days have elapsed since the most recent trigger/reminder, and at
  least one person is still outstanding.
- Posting a reminder resets the clock (it's a new marker comment).
- "Working days" = Mon–Fri excluding Singapore public holidays (reused from
  `mo_trigger_comment.logic.SG_HOLIDAYS`).

---

## CLI
```
python -m tasks.costing_hs_code_trigger.main --mock             # default (VPS)
python -m tasks.costing_hs_code_trigger.main --live             # laptop, posts
python -m tasks.costing_hs_code_trigger.main --live --dry-run   # decide, no post
python -m tasks.costing_hs_code_trigger.main --mock --today 2026-07-20  # test date
python -m tasks.costing_hs_code_trigger.main --mock --only NPIOTHER-4566
```
Prints a per-container decision table and a summary
(triggered / reminded / waiting / complete / skipped).

---

## Architecture Rules (same as the rest of the suite)
1. `--mock` default; mock reads `mock_data/`; live hits JIRA.
2. Credentials via `core/config_loader.py`. Never hardcode.
3. Pure decision logic in `logic.py`; all JIRA I/O in `main.py`.
4. Reuse `core/jira_client.py`, `core/logger.py`.
5. Reuse pure helpers from `tasks/mo_trigger_comment/logic.py`
   (`find_wp_by_name`, `jira_mention`, `extract_order_type`,
   `order_type_label`, `is_working_day`, `add_working_days`, `SG_HOLIDAYS`).
6. Errors: log and continue, never crash silently.

---

## Message Templates (config-driven, token substitution)

Tokens replaced at render time (`{token}`):
- `{costing_mentions}` — comma-joined mentions of the costing people
  (initial trigger: all; reminder: only outstanding costing people)
- `{hs_mention}` — the HS Code person's mention (blank in a reminder when done)
- `{outstanding_lines}` — reminder only: one line per track that still has
  outstanding people, e.g. `Costing: [~a], [~b]` / `HS Code: [~c]`

The marker footer (`trigger_marker` / `reminder_marker`) is appended by
`main.py` to every posted comment — templates should NOT include it.

---

## Files
```
tasks/costing_hs_code_trigger/
├── TASK.md         ← this file
├── main.py         ← CLI, JIRA I/O, orchestration, posting
├── logic.py        ← pure logic: trigger gate, done detection, reminder-due,
│                     message assembly, marker/timestamp parsing
├── capture.py      ← save mock data on company laptop
├── test_logic.py   ← pure-logic unit checks (run on VPS, no JIRA)
└── mock_data/      ← fixtures (containers.json, issue_*.json, wps_*.json)
```

---

## Config (add to config/config.example.yaml)
```yaml
costing_hs_code_trigger:
  costing_people:
    - { username: "", display: "Costing Person 1" }
    - { username: "", display: "Costing Person 2" }
  hs_code_person: { username: "", display: "HS Code Person" }
  ready_resolutions: ["Done", "Acknowledged", "Won't Do"]
  done_keywords: ["done", "updated", "completed"]
  negation_guards: ["not done", "no update", "pending"]
  reminder_interval_working_days: 2
  trigger_marker: "#Ref: CostHS-Trigger#"
  reminder_marker: "#Ref: CostHS-Reminder#"
  messages:
    trigger: |
      Hi {costing_mentions}, please update the *Costing* for this container.
      {hs_mention}, please update the *HS Code*.

      Kindly reply on this ticket with "Done" once completed.
    reminder: |
      Reminder — the following updates are still pending:
      {outstanding_lines}

      Kindly complete and reply "Done" on this ticket.
```
Usernames are the JIRA logins used for `[~username]` mentions; fill them in on
the company laptop (like `mo_trigger_comment`). If a username is blank the
person still appears by display name but is not a clickable mention **and can
never be auto-detected as done** — so usernames are required for the reminder
loop to converge.

---

## Edge Cases
1. Container already has trigger marker → never re-trigger; evaluate reminders.
2. DMR container → trigger immediately regardless of WPs.
3. Non-DMR missing a prerequisite WP → not ready → skip (log reason).
4. Person's username blank in config → can't detect done → keeps reminding
   (surfaced as a warning).
5. "not done"/"pending" in a person's comment → negation guard, not counted.
6. Reminder not yet due (< interval working days) → waiting, no post.
7. All three done → complete, no action.
8. `--dry-run` → prints the comment(s) that would post, posts nothing.

## Acceptance Criteria
- [ ] Non-DMR container triggers only when all 5 WPs are Done/Won't Do
- [ ] DMR container triggers immediately when in scope
- [ ] Trigger comment tags all 3 people and asks them to reply Done
- [ ] Done detected per person from their own comment after the trigger
- [ ] Reminder re-tags only outstanding people, grouped by track
- [ ] Reminder fires only after >= 2 working days since the last nudge
- [ ] Duplicate trigger marker prevents re-triggering
- [ ] All-done container produces no further comments
