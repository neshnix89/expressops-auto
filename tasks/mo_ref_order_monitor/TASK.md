# Task: mo_ref_order_monitor

## Purpose
Poll the M3 "Ref order no" field (`MWOHED_AP.VHRORN`, the P1/PMS100 MO header)
every 30 min for each active MO and keep the JIRA Work Container up to date with
fine-grained build progress — replacing the once-a-day Excel→Jira Publisher.
Production updates that field as each major process completes, and appends `IS`
to it when the run has a problem.

**Status: LIVE** (fleet-wide since 31-Jul-2026) on the primary laptop.

## Category
General

## Trigger
Scheduled poller, every 30 min, 08:00–17:00, via Windows Task Scheduler.
Per MO, polling continues until the JIRA container is closed (resolution set).

## Systems Involved
- [x] JIRA — read — container universe (JQL), container comments (MO→container map), description
- [x] JIRA — write — container **description** (tracking table + dwell summary)
- [x] M3 ERP (ODBC) — read — MO header via `PFODS.MWOHED_AP`
- [x] Webex — write — issue alerts, via the **desktop app** (org blocks bots/integrations)

---

## Outputs

### 1. `MO BUILD TRACKING - {mo}` table (one row per day)
```
||Day||Ref Order No||Changes||Stages that day||
|30-Jul|AOI|2|JIRA → GERALD → AOI|
```
End-of-day ref order no, how many times it changed that day, and the ordered
stages seen. Regenerated from state on every write (no row parsing).

### 2. `MO BUILD DWELL - {mo}` summary (published when the MO closes)
Per stage: distinct **working days** touched, the daily hour breakdown, and the
total. `2d, 4h` = 2 working days, 4 working hours total — **not** a 9h
conversion.

### 3. Webex alert — ISSUE-GATED, not on every change
An issue belongs to the **stage it is flagged on**: `AOI-IS` and `PACK-IS` are two
different problems. The tracked identity is the (stage, IS) pair, so:
- no-IS → IS: **issue_raised** 🔴
- IS → no-IS: **issue_cleared** 🟢 (reports how long the issue was open)
- IS → IS on a **different** stage: **issue_moved** 🔁 — the old issue ended and a
  new one was raised, in one post (reports how long the old one lasted)
- IS → IS on the **same** stage (`AOI-IS` → `aoi is`): silent, still one issue
- MO close: **closed** ✅ · re-open: **reopen** 🟠
Routine stage changes send nothing. JIRA still records every change.
Multiple pending alerts post as ONE grouped multi-line message.

---

## Lifecycle
- Status < 80 → publish. First poll of a new day writes a heartbeat row even
  with no change.
- Status 80/90 → write `CLOSED Sts N` + dwell summary, then go quiet.
- Keeps polling silently after close; a drop back below 80 is a **re-open** and
  publishing resumes.
- **Baselining:** an MO seen for the FIRST time already at 80/90 finished before
  the monitor existed — recorded silently, no row, no alert
  (`baseline_closed_on_first_sight`, default true).
- Container closed (resolution set) → abandon the MO.

---

## CONFIRMED — M3 mapping (discover_mo_header.py, MO 7003904788)
Table `PFODS.MWOHED_AP` (123 cols). ODBC path confirmed; no H5 scraping needed.

| Column | Meaning | Sample |
|--------|---------|--------|
| `VHMFNO` | MO number (lookup key) | `7003904788` |
| `VHPRNO` | Product number | `70209808` |
| `VHWHST` | **MO status — the 80/90 gate** | `90` |
| `VHWHHS` | Highest status ever reached | `90` |
| `VHRORC`/`VHRORN`/`VHRORL` | Ref order no (3 boxes) — **only `VHRORN` is tracked** | `0` / `QM` / `2902` |
| `VHORTY` | Order type | `SPI` |
| `VHRESP` | Responsible | `MP-3459` |
| `VHLMDT`/`VHCHNO`/`VHCHID` | Last-modified / change# / by — **ODS replica freshness** | |

Real observed `VHRORN` values: `QM`, `WW`, `0536`, `AOI`, `TP-IS`, `PACK-IS`,
`S.S-IS`, `GERALD`, `JIRA`, `BREAK`. Free text — treat as opaque, never parse.

**No per-field change history in M3.** `VHLMDT` only flags that the header
changed at all, so the poller builds its own dwell history. `MWOHED_AP` is an
ODS *replica* — a fresh P1 edit can lag; `VHLMDT`/`VHCHNO` are logged each poll
so lag is distinguishable from a genuine no-change.

---

## Coexistence with the legacy Excel→Jira table
- Legacy heading `h2. MO BUILD STATUS - {mo}` is **never** matched or edited.
  Ours is `h2. MO BUILD TRACKING - {mo}` (+ `h3. MO BUILD DWELL - {mo}`).
- New sections append at the END of the description → render **below** the
  legacy table.
- Section replacement is line-based and stops at the next wiki heading, so
  legacy tables, other MOs and manual PIC notes are never consumed.
- **Self-heal:** the legacy tool PUTs the whole description; if a concurrent
  write drops our section, the next poll restores it.
- **Legacy patched** (31-Jul-2026) via `scripts/patch_legacy_excel_to_jira.py`
  so it bounds its section at the next heading instead of deleting everything
  below it. Applied to `…\Automation\Excel to Jira_V3\src\backend\`.

---

## Two-laptop operation
Both machines point `state_dir` **and** `webex.queue_file` at the same shared
network path:
`Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering\`

Shared state is what makes this safe: whichever laptop polls first sees the
latest history, so the two never overwrite each other's tables, an alert is sent
once, and either machine covers when the other is off. Schedules are offset —
primary `:00/:30` (08:00–17:00), second `:15/:45` (09:15–17:00).

**`pilot_containers` is NOT shared** — it lives in each machine's `config.yaml`.
Change it on BOTH or they will disagree about which containers to maintain.
`install_second_laptop.ps1` therefore has **no default scope**: it requires
`-FleetWide` or `-PilotContainers` and refuses to guess. Its old default (two
NPIOTHER keys) had already gone stale against a fleet-wide production.
Production is **fleet-wide** (`pilot_containers: []`) as of 13-Aug-2026.

---

## Operational lessons (all learned the hard way — do not re-discover)
0. **Windows still has a 260-character path limit.** `Copy-Item` (PS 5.1) dies
   on longer paths with a misleading "Could not find a part of the path";
   `robocopy` does not. The shared-drive staging prefix is 129 characters
   before any repo folder, so a long `mock_data` filename tips it over (262 —
   it stopped a second-laptop install). The installer copies with robocopy.
1. **Task Scheduler defaults break laptops.** `DisallowStartIfOnBatteries` is ON
   and `StartWhenAvailable` is OFF by default; `schtasks.exe` can set neither.
   Always register via `scripts/setup_mo_ref_order_schedule.ps1`.
2. **`sync_now` overwrites the runner .bat but never `config.yaml`.** Anything
   operators tune (pilot scope) must live in config.yaml, or it reverts.
   The flip side bit on the first second-laptop install: staging the repo to
   the share with `robocopy /MIR` carried the primary's `config.yaml` — PAT and
   all — into a `_Public` folder and then onto the colleague's machine, where
   the installer adopted it silently and never prompted. **Never copy
   `config.yaml` between machines**; the installer now excludes it. Prompts for
   a token use `-AsSecureString` for the same reason — a plain `Read-Host`
   echoes it into scrollback and into any screenshot of the install.
3. **No pilot scope = FLEET-WIDE.** Logged as an explicit warning, because
   silence once meant both "by design" and "config not loaded".
4. **A `--dry-run` must not persist anything** — not state, not the Webex queue.
   Both caused real incidents (phantom history; duplicate alerts).
5. **Focus is not delivery.** The desktop transport used to verify the chat
   window had focus *by window handle* (a PID check passes for image-preview
   windows) and then type blind. That is not enough: if the space is still
   switching, Webex discards the compose contents and the Enter posts nothing —
   silently, with the log reporting success. **Two alerts were lost this way on
   13-Aug**, one a RESOLVED notice a colleague waited an hour for.
   Fixed 13-Aug: the message is **pasted** from the clipboard and the compose
   box is **read back** (Ctrl+A/Ctrl+C, compared against the payload, with a
   clipboard sentinel so an empty copy cannot fake a match) **before** Enter.
   Mismatch ⇒ re-paste, up to 3 attempts, then exit 6 with nothing sent and the
   alert left queued. Also: re-opening the deep link per message re-renders the
   compose box and loses text, hence one visit + one grouped post per flush.
   **Webex swallows the first keystroke after a window is activated.** A primer
   (space, backspace) before the real input is mandatory — without it the whole
   Ctrl+V was eaten and every attempt read back empty. This is the same swallow
   that used to clip the opening characters of a typed message; the primer was
   dropped by accident in the switch to pasting and had to be restored.
   Verified working 13-Aug 15:12 — first attempt, no retries.
9. **`$ErrorActionPreference = "Stop"` makes `Write-Error` terminating**, so
   every `exit <code>` after one is dead code and PowerShell returns 1. Exit
   codes must be written to stderr by hand or the caller cannot tell a refusal
   from a crash — and Python retried the refusal, having been told it was
   transient.
6. **A locked screen cannot be typed into, and out-of-hours alerts wait.**
   The desktop transport needs an unlocked session. An alert raised at 18:31 or
   on a Friday sits queued until someone unlocks — 60+ hours over a weekend.
   The original 12h `max_age_hours` therefore DESTROYED two real alerts
   (28-Aug 16:56, 31-Aug 18:31) before anybody could have seen them. Now: 72h
   default, a lock is detected up front (exit 8, no misleading "focus held by
   ''"), and a late alert is delivered prefixed `(delayed 14h)` so it reads as
   history rather than news. Only the webhook/bot transport can post to a
   locked machine — one more reason to chase I2607-2336.
7. **JIRA wiki markup in cells.** A leading `#` renders as a numbered list; bare
   `|` splits the row. `VHRORN` is free text, so every cell is sanitised.
8. **Pasting removed the escaping problem.** Typing needed SendKeys escaping for
   emoji and `( ) % [ ]`, and Shift+Enter for internal line breaks. The payload
   is now RAW text — do not re-introduce escaping, or the braces get pasted
   verbatim.

---

## Files
```
tasks/mo_ref_order_monitor/
├── TASK.md                  ← this file
├── logic.py                 ← pure lifecycle + dwell + table rendering
├── m3_mo.py                 ← MWOHED_AP -> Observation
├── state.py                 ← per-MO JSON state (atomic write)
├── webex.py                 ← queue + 3 transports (desktop/webhook/bot)
├── send_webex_desktop.ps1   ← chat-window targeting, focus verify, paste + read-back
├── main.py                  ← orchestration (mock/live/dry-run)
├── capture.py               ← read-only mock-data capture
├── discover_mo_header.py    ← read-only M3 discovery (accepts an MO number)
└── discover_webex_rooms.py  ← read-only Webex room list (needs a token)

repo root / scripts/
├── diagnose.bat             ← DOUBLE-CLICK health report -> logs\diagnose.txt
├── run_mo_ref_order_monitor.bat / _portable.bat
├── scripts/setup_mo_ref_order_schedule.ps1   ← laptop-safe task registration
├── scripts/set_pilot.py                      ← safe pilot-scope edits
├── check_access.bat         ← DOUBLE-CLICK on a new machine -> one IT request
├── scripts/check_access.py                   ← what this account can actually reach
├── scripts/install_second_laptop.ps1         ← one-shot second-machine setup
└── scripts/patch_legacy_excel_to_jira.py     ← legacy publisher fix
```

## CLI
```
--mock | --live            mode (mock default, never writes)
--dry-run                  fetch + compute, write nothing (state/queue untouched)
--container KEY[,KEY]      override pilot scope for one run
--map MO=CONTAINER         force a pairing, skip comment-scan (repeatable)
--only MO                  restrict to one MO
--reset                    wipe state for in-scope MOs (needs a selector)
--now "YYYY-MM-DD HH:MM"   override poll time (testing)
--test-webex TEXT          send one message via the configured transport
```

## Routine checks
- **`check_access.bat`** on any NEW machine, BEFORE asking IT for anything.
  It separates access from setup: EDM authenticates with the Windows/SSO
  identity, so viewing EDM in the app means the ACCOUNT is already fine —
  Python still needs `EDMAdmin.exe` because the Oracle logon trigger rejects
  connections by program name. That is `setup_edmadmin.bat`, never a ticket. It
  tests Python packages, the JIRA token (and reports **whose** it is), the M3
  ODBC login plus each table individually, read **and write** on the shared
  folder, and the Webex app — then prints every failure as one ready-to-paste
  IT request. Piecemeal discovery turns a one-ticket setup into three.
- **`diagnose.bat`** after any sync, and after any config edit. It reports code
  freshness, the parsed config, shared-state reachability, the scheduled task's
  battery/catch-up flags, and runs a read-only dry run. Safe to paste — secrets
  show as `SET (n chars)`.
- Logs: `logs\mo_ref_order_monitor_run.log` (appended) and
  `logs\mo_ref_order_monitor.log` (**overwritten each run** — last run only).

## KNOWN EXPIRY RISK — M3 credentials (raised 13-Aug-2026)

IT (Mannheim global password policy) is **removing stored Oracle credentials
from ODBC DSNs**. A newly-built laptop already has `ODSSG` with a blank
`UserID` — by design, not a misconfiguration — and the same will be applied to
the existing primary laptop.

**When it reaches `TMOGHANAN`, every M3-dependent task stops** with
ORA-01017 until a replacement authentication method is in place. That is the
one dependency here with an externally-imposed deadline nobody on this side
controls. Track the date.

Replacement options, in order of preference:
1. **OS / Windows authentication** — nothing stored, nothing to rotate. Already
   proven here: `core/edm.py` connects to EDM Oracle with `externalauth=True`.
2. **Oracle Wallet / external password store** — needs no code change either:
   `M3Client` already connects as plain `DSN=ODSSG` with no username when
   `m3.user` is blank, which is exactly what a wallet expects.
3. **Application-supplied credentials** — `m3.user` / `m3.password` in
   config.yaml (supported since 13-Aug). Works, but it is still a stored
   credential and may not satisfy the policy's intent. Do not lead with it.

## Open items
- [ ] **IT ticket I2607-2336** — Webex bot / Incoming Webhooks approval. Once
      granted, `transport: "webhook"` is a one-line change and removes the whole
      UI-driving path. Still worth chasing: the read-back proves the text was in
      the compose box, not that Webex accepted the post.
- [ ] Retire the legacy Excel→Jira tool once the team is confident in this one.
- [ ] Second laptop rollout.
