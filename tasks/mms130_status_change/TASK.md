# Task: MMS130 Status Change

## Purpose
Bulk-change balance-ID status to **1 (Under inspection)** in M3 MMS130
(Balance Identity. Reclassify) from an Excel list, using the same Playwright /
H5 approach as `to_status_check` (XDRX800) and `mo_trigger_comment` (XECX450).

## Systems
- [x] M3 H5 (Playwright, headed Edge, ADFS SSO) — **WRITE**

## Input
Excel (.xlsx) or CSV, first sheet (or `--sheet`), header row:

| Item number | Lot number | Warehouse | Location | Container (optional) |

Aliases accepted: PN / Part number, Lot / BANO, WHS / WHLO, Loc / WHSL.
Rows with a missing required value are reported as SKIPPED.

## Flow (per row)
1. MMS130/A: fill Item number, Lot number, Warehouse, Location, (Container);
   Calc method = `1-Change status location level` → NEXT
2. MMS130/B: set the new status field to `1` → NEXT → back on panel A = OK

Fields are found by their on-screen label (not H5 element ids), so the
script does not depend on MMS130 field names. The panel is tracked via the
`MMS130/A` / `MMS130/B` footer.

## Safety
- `--live` without `--apply` is a **dry run**: panel A is validated by M3
  (NEXT), panel B is read, then the script goes back without confirming.
- `--apply` asks you to type `YES` (skip with `--yes`).
- Any unexpected panel/dialog stops the whole run; screenshots go to
  `outputs/mms130_status_change/debug_<timestamp>/`.

## Run (company laptop)
```
python -m tasks.mms130_status_change.main --mock --input list.xlsx          # check the sheet
python -m tasks.mms130_status_change.main --live --discover --input list.xlsx  # first time: dump panel A/B
python -m tasks.mms130_status_change.main --live --input list.xlsx --limit 1   # dry run 1 row
python -m tasks.mms130_status_change.main --live --input list.xlsx --apply --limit 1
python -m tasks.mms130_status_change.main --live --input list.xlsx --apply
```
or `run_mms130_status_change.bat <file> [extra args]`.

Other flags: `--start-row N`, `--status-label "New status"` (if panel B's
field isn't auto-detected), `--keep-open`.

## Output
`outputs/mms130_status_change/mms130_results_<timestamp>.xlsx` — one line
per input row with Result (OK / DRY-RUN OK / ERROR / SKIPPED), M3 message,
and a snapshot of panel B values.

## Discovery notes
- [ ] Confirm MMS130 opens in the main H5 page vs an iframe (driver handles both).
- [ ] Confirm panel B label for the new status (default candidates:
      New status, New balance ID status, To status, Balance ID status, Status).
- [ ] Confirm what M3 does after confirming B (expected: back to panel A).
