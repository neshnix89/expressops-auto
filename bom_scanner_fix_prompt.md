Three changes needed for bom_scanner. Read tasks/bom_scanner/main.py and
tasks/bom_scanner/publish.py before making changes.

CHANGE 1: Separate scan from comment.
The current code scans AND posts JIRA comments in one run. Wrong workflow.
The operator wants to:
  1. Scan → publish results to Confluence page ONLY (no JIRA writes)
  2. Review the Confluence page manually
  3. Selectively push JIRA comments on chosen containers

Restructure main.py CLI into two commands:

  python -m tasks.bom_scanner.main scan --live --target-status 310 --source both
    → Runs the full scan (Phase A + B), publishes results to Confluence page.
    → NEVER posts JIRA comments. Read-only against JIRA (search + get_issue).
    → This is the default/primary command.

  python -m tasks.bom_scanner.main comment --live --target-status 310 --keys POSX-6558 NPIOTHER-3673
    → Posts ONE aggregated JIRA comment on each specified container.
    → Uses the scan results already computed (re-scans those specific containers).
    → Requires explicit --keys with container keys to comment on.
    → Checks for "(Automated by BOM Scanner)" marker — skips if already commented.

For backward compatibility, running without a subcommand defaults to "scan":
  python -m tasks.bom_scanner.main --live --target-status 310
    → same as "scan"

CHANGE 2: One comment per container (for the "comment" command).
When posting a JIRA comment, aggregate ALL flagged articles into a single
comment body. Structure:

[~reporter] BOM PLC Check — the following components have PLC status != {target}:

*Article 70194351:*
|| Component || PLC || Description ||
| 123456 | 200 | Some Part |
| 789012 | NEW | Another Part |

*Article 70194352:*
|| Component || PLC || Description ||
| 345678 | INT | Third Part |

Please update the PLC status to {target} before proceeding with MR.
_(Automated by BOM Scanner)_

Only include articles that have flagged components. One comment total per
container, regardless of how many articles.

CHANGE 3: M3 validation gate.
Before running BOM_FLAGGED_SQL for any article number, verify it exists as
a real product structure:

    SELECT COUNT(*) FROM PFODS.MPDHED
    WHERE PHPRNO = ? AND PHSTRT = 'STD' AND PHFACI = 'MF1'

If count = 0, skip — log "article XXXXXX: no STD product structure, skipping".
This filters out false-positive numbers (cost centers, references) that the
regex extracts but aren't actual M3 products.

CHANGE 4: Add --dry-run flag to both commands.
  --dry-run: print what would be done but don't write to JIRA or Confluence.
  Works in both scan and comment modes.
  In scan mode: shows the results table but doesn't update the Confluence page.
  In comment mode: shows which comments would be posted but doesn't post.

Update main.py, logic.py, and publish.py as needed. Do not change capture.py.
Keep --mock/--live split working as before.
