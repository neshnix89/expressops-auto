# KPI overlay vs Tableau — validation and data-source migration

**Status:** code ready, **not yet validated against live data**. Everything below
that says "unknown" is unknown because the fact tables are only reachable from
the company laptop, and nothing here was run against them yet.

Two jobs, in this order:

1. **Validate** — does the overlay's KPI arithmetic agree with Tableau's?
2. **Migrate** — stop computing the KPIs here and read Tableau's instead.

They are one workflow: the validation is what makes the migration safe, and the
same diff stays switched on afterwards as a regression check.

---

## 0. Where the credentials go

`config/config.yaml`, which is **gitignored** and never leaves the laptop:

```yaml
kpi_warehouse:
  user: "sync_user"
  password: "<the password from the BI email>"
```

That is the only place. Not in a `.bat`, not in a scheduled-task argument, not
in anything under `scripts/`. Copy the block from
[`config/config.example.yaml`](../config/config.example.yaml) — it carries the
rest of the settings with comments.

**Also do these two things:**

- Delete the BI team's email, or at least move it out of the inbox. A password
  sitting in mail is the usual way these leak.
- The Tableau PAT secret in `docs/WORKLOG.md` was committed in plaintext at one
  point. It is redacted in the file now, but git history still has it — **that
  PAT should be rotated** regardless of this migration.

---

## 1. What is actually being compared

Tableau's "ExpressOps KPIs" workbook (#3651) does not compute these KPIs. It
renders three fact tables that a scheduled job computes:

| Table | Grain |
|---|---|
| `Fact_pm_npi_wc_kpi` | one row per Work Container |
| `Fact_pm_npi_wp_kpi` | one row per Work Package |
| `Fact_pm_npi_wc_wp_combined` | WC joined to its WPs |

The overlay computes the same numbers a second time, from JIRA, in
[`core/kpi_core.py`](../core/kpi_core.py) and
[`tasks/kpi_overlay/logic.py`](../tasks/kpi_overlay/logic.py). Two
implementations of one definition is the whole problem: they can disagree, and
today nobody can say whether they do.

**The warehouse job's source code is not in this repo.** So this is not a code
review — there is no second formula to read. The only honest validation is to
run both over the same containers on the same day and diff the numbers, which
is what `scripts/validate_kpi_vs_tableau.py` does.

---

## 2. The eleven places the two can legitimately differ

This is the checklist to walk with whoever owns the warehouse job. Each item is
a real difference the overlay's code will produce **if** the warehouse made the
other choice. Ordered by how likely they are to bite.

### 2.1 The "KPI method" −1 — *most likely single cause of a uniform offset*
`fNetWorkdays` ([`core/kpi_core.py:193`](../core/kpi_core.py)) counts weekdays
from start to end **inclusive**, subtracts holidays, then subtracts 1
([line 214](../core/kpi_core.py)) so the start day counts as day 0. A warehouse
`DATEDIFF`-style calculation usually does **not** do that.

*Symptom:* every container is off by exactly the same ±1.
*Test:* the elapsed-delta histogram collapses to a single non-zero bucket.

### 2.2 Holiday calendars — *the latent time bomb*
`HOLIDAYS` ([`core/kpi_core.py:98`](../core/kpi_core.py)) is a hardcoded set of
**2026 dates only**, one calendar per site. A warehouse will have a proper date
dimension.

Two distinct risks:
- The two holiday lists differ today → containers spanning a holiday are off by
  1–5 days while others match exactly (a *spread*, not a constant offset).
- **From 1 Jan 2027 the overlay has no holidays at all** and will silently
  overstate every elapsed count. This is a bug whether or not the migration
  happens; migrating to the warehouse removes it, which is a real argument for
  migrating.

### 2.3 Targets
`TARGETS_V5` ([`core/kpi_core.py:58`](../core/kpi_core.py)) carries **two
deliberate corrections** against the legacy table:

| Bucket | Legacy | This repo | Why |
|---|---|---|---|
| Singapore Documentation | 1 | **4** | Long-flagged bug; the legacy live overlay hardcoded 4 in its own config, masking it |
| Trutnov Logistics | 4 | **1** | Confirmed target, user decision during the migration |

If the warehouse still holds the legacy values, those cells will disagree — and
that is a **business decision**, not a bug to patch. Someone has to say which
table is official. Full current table:

| Bucket | Singapore | Trutnov |
|---|---|---|
| Overall (T_NPI) | 24 | 21 |
| Material / PCB | 15 / 15 | 15 / 15 |
| Routing / PE / TE TechnPrep | 5 | 5 |
| SMT Build | 5 | 5 |
| Logistics | 4 | 1 |
| Documentation | 4 | 1 |

### 2.4 Scope — which containers are on the board at all
The overlay's JQL ([`tasks/kpi_overlay/main.py:79`](../tasks/kpi_overlay/main.py)):

```
issuetype = "Work Container" AND "Product Type" = "SMT PCBA"
AND "NPI Location" in ("Singapore","Trutnov") AND resolution is EMPTY
```

The fact table has its own idea of "running" — the workbook has separate
**Work Container (Running)** and **(Closed)** views, so there is a flag or a
filter in there somewhere. If the two populations differ, pills appear or
vanish, which users notice long before they notice a day-count being wrong.

`source_tableau.py` filters closed rows conservatively (resolution / resolved
date / status). Once discovery names the real flag column, pin it exactly:

```yaml
kpi_warehouse:
  row_filter:
    wc_kpi_state: ["Running"]
```

### 2.5 Parking
`elapsed_wd` ([`tasks/kpi_overlay/logic.py:107`](../tasks/kpi_overlay/logic.py))
handles **multiple** park/unpark cycles, subtracts each closed park's overlap,
and freezes elapsed at the last park's start when a container is currently
parked. `core/kpi_core.py` also still carries `parking_adjusted_workdays`, the
older **single-pair 9-case** Access algorithm, kept for the retired weekly
pipeline and unused by the overlay.

Which of the two the warehouse implements is unknown. Note also that the
subtraction mixes conventions: the window is measured with the −1-adjusted
`fNetWorkdays` and the parked overlap with the inclusive `netWorkdaysRaw`.
That is intentional but it is exactly the sort of thing two implementations get
differently, so **check parked containers separately** — they will not follow
whatever pattern the unparked ones show.

### 2.6 NPI start anchor
`npi_start = min(created)` over the container's **active** official WPs
([`logic.py:348`](../tasks/kpi_overlay/logic.py)) — WPs resolved as Won't
Do/Cancelled are excluded from the anchor. Plausible warehouse alternatives:
the container's own created date, or the min over *all* WPs. A different anchor
shifts elapsed for that container only, so it shows up as a spread.

### 2.7 Yellow has no warehouse equivalent
`YELLOW_THRESHOLD = 2` ([`logic.py:41`](../tasks/kpi_overlay/logic.py)): a
container within 2 working days of its target is amber. The fact tables carry a
hit/miss verdict, which is binary. **This is expected to differ and is not a
defect** — the validation script reports colour-only differences separately for
exactly this reason.

### 2.8 The tech-prep rules are overlay-only
For Routing / PE / TE ([`logic.py`](../tasks/kpi_overlay/logic.py) `compute_wp_kpis`):
- **Done:** Green if within target **or** resolved on/before Material fullset.
- **Running:** Green if elapsed ≤ **3** — a hardcoded 3, not the target of 5
  ([`logic.py:236`](../tasks/kpi_overlay/logic.py)) — else Green/Red against the
  target, or Yellow when Material fullset is unknown.

Material fullset = `max(Material resolved, PCB resolved)`, and only when **both**
are Done ([`logic.py:166`](../tasks/kpi_overlay/logic.py)).

The Tableau-sourced path deliberately does **not** reimplement this; it uses the
plain elapsed-vs-target rule and surfaces the warehouse's own `wp_target_hit`
instead. Expect tech-prep WPs to be where the WP-level colours differ.

### 2.9 Dependent WP start dates
Not every WP starts when it was created:
- SMT Build starts at Material fullset.
- Logistics and Documentation start at **SMT Build's resolution date**.
- A WP whose predecessor has not finished is `waiting` → grey, no number.

If the warehouse measures these from WP creation instead, those three WPs will
be systematically higher there.

### 2.10 As-of date and staleness
The overlay recomputes against `date.today()` at run time. The fact tables are
refreshed on a schedule, so their "running" durations are as of the last ETL.
Run the daily overlay before that refresh and every running container is a day
behind.

Guarded by `kpi_warehouse.max_staleness_hours` (default 30): if the newest
as-of row is older than that, the run logs a loud warning instead of silently
publishing stale pills. **Ask the BI team when the job runs** and schedule the
overlay after it.

### 2.11 Skipped work packages
`is_skipped` ([`logic.py:184`](../tasks/kpi_overlay/logic.py)): resolved but the
resolution is not Done/Acknowledged (Won't Do, Cancelled) → grey pill, excluded
from the NPI-start anchor and from `wpsDone`. Whether the fact table keeps,
drops, or counts those rows changes both the WP row-set and `wpsDone`/`wpsTotal`.

---

## 3. Runbook

Everything below runs on the **company laptop**. Nothing writes to JIRA,
Confluence or the database.

### Step 1 — credentials
Put `user` / `password` under `kpi_warehouse` in `config/config.yaml` (§0).

### Step 2 — discovery
```
run_kpi_discovery.bat
```
(or `python scripts\kpi_warehouse_discovery.py`)

It prints, and writes to `outputs/kpi_discovery/`:

1. what config is holding (password shown only as `set` / `(blank)`);
2. this machine's ODBC drivers and DSNs;
3. the published data sources **and the database connection behind each one** —
   `server=HOST:PORT`, which is the piece the BI email did not include and the
   ODBC route needs;
4. every route tried and why each failed;
5. every column of every fact table, next to the logical field it resolved to,
   plus the columns nothing claimed.

**The report contains no password and is safe to send to IT or BI as-is.**

Then, from the report:
- set `kpi_warehouse.driver` to whichever route worked, so runs stop probing;
- paste any UNRESOLVED logical field into `kpi_warehouse.columns.<table>`;
- if nothing worked, section 3 of the report tells the BI team exactly what is
  missing (almost certainly the database host, or a DSN).

### Step 3 — capture fixtures (optional, one line)
```
python scripts\kpi_warehouse_discovery.py --save-mock
```
Overwrites `tasks/kpi_overlay/mock_data/warehouse/*.json` with real rows, so
`--mock` development stops working against invented data.

### Step 4 — validate
```
run_kpi_validation.bat
```
(or `python scripts\validate_kpi_vs_tableau.py --live --md`)

Computes today's KPIs **both** ways, diffs them container by container and WP by
WP, and interprets the result against §2 — naming the likely cause rather than
just printing numbers. Writes JSON + a markdown table for Confluence to
`outputs/kpi_validation/`.

Read it in this order: **scope → elapsed → targets → colour.** A scope
difference makes every number below it meaningless, so fix that first.

### Step 5 — run both for a week
```yaml
kpi_overlay:
  source: both
  source_of_truth: jira        # keep publishing today's numbers while watching
```
Every daily run then logs the full diff and writes
`outputs/kpi_source_diff.json`. One day's agreement is not evidence; parked
containers, holidays and month boundaries only show up over time.

### Step 6 — switch over
```yaml
kpi_overlay:
  source: tableau
```
Or keep `both` with `source_of_truth: tableau` and a `max_disagreement` guard —
that publishes Tableau's numbers but keeps the standing comparison, and refuses
to publish at all if the two ever diverge past the threshold. That is the
better end state while confidence is still being built.

---

## 4. What changed in the code

| File | What |
|---|---|
| `core/kpi_warehouse.py` | **new** — reads the three fact tables. Route (Tableau VDS / ODBC / DSN-less ODBC) and column names both resolved at run time, not hardcoded |
| `tasks/kpi_overlay/source_tableau.py` | **new** — turns fact rows into the existing cache shape |
| `tasks/kpi_overlay/compare.py` | **new** — the JIRA-vs-Tableau diff, shared by the validator and `--source both` |
| `tasks/kpi_overlay/main.py` | `--source jira\|tableau\|both`; default from `kpi_overlay.source` |
| `scripts/kpi_warehouse_discovery.py` | **new** — the one-shot discovery report |
| `scripts/validate_kpi_vs_tableau.py` | **new** — the validation report |
| `tasks/kpi_overlay/test_source_tableau.py` | **new** — 41 offline checks, incl. that the diff actually detects an off-by-one, a target mismatch and a scope mismatch |
| `run_kpi_discovery.bat`, `run_kpi_validation.bat` | **new** — double-click runners, path-portable |
| `config/config.example.yaml` | `kpi_warehouse` (credentials live here) and `kpi_overlay` sections |

**`kpi_cache.json` keeps its shape.** The Tampermonkey userscript is a pure
renderer and needs no change — it only gains a `source` field it can ignore.
The default is still `jira`, so nothing changes until the config says so.

### Known gaps in the Tableau path
Honest list, because these are invisible until someone looks for them:

- `parkingPeriods` and `workPackages` (the full child list) come back **empty** —
  the fact tables publish the net number, not the spans behind it. If the
  userscript's tooltip uses either, that detail is lost.
- WP pill colour uses the plain elapsed-vs-target rule; the tech-prep secondary
  rule (§2.8) is not reproduced.
- The candidate column names in `core/kpi_warehouse.py` are **guesses** seeded
  from the 2026-05-29 CSV peek. Discovery is what turns them into facts; until
  then, treat any `--source tableau` output as unverified.
