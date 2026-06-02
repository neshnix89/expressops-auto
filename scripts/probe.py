"""
DISCOVERY PROBE — read-only scratchpad. EDIT THIS each discovery iteration.

CURRENT PROBE: diagnose why the mo_trigger Confluence staging page isn't
updating with the latest info. Reads the live page (GET only) and reports:
  - the real last-updated version + timestamp + author (is publishing even
    happening, or is the page stuck at an old version?)
  - the "Run time" printed in the page's own Summary table
  - how many comment bodies are frozen on the page, and a date sniff of each
    (if publishing IS happening, frozen-but-stale bodies point at the
    _merge_preserved_bodies logic freezing content after first publish)

All read-only — the guard blocks any write.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import load_config
from core.confluence import ConfluenceClient


def main() -> None:
    cfg = load_config(mode_override="live")
    page_id = cfg.pages.get("mo_trigger_comment")
    print(f"configured page id: {page_id!r}")
    if not page_id:
        print("  -> no page id in config.pages['mo_trigger_comment']; nothing to check.")
        return

    conf = ConfluenceClient(cfg)
    page = conf.get_page(page_id)  # expand=body.storage,version by default

    ver = page.get("version") or {}
    print("\n--- live page state ---")
    print(f"  title        : {page.get('title')}")
    print(f"  version      : {ver.get('number')}")
    print(f"  last updated : {ver.get('when')}")
    print(f"  updated by   : {(ver.get('by') or {}).get('displayName')}")
    if ver.get("message"):
        print(f"  version msg  : {ver.get('message')}")

    body = ((page.get("body") or {}).get("storage") or {}).get("value", "") or ""
    print(f"  body length  : {len(body)} chars")

    # "Run time" cell the publisher stamps into its Summary table
    m = re.search(r"Run time</th>.*?<tr>\s*<td>(.*?)</td>", body, re.DOTALL)
    print(f"\n  Summary 'Run time' on page : {m.group(1).strip() if m else 'NOT FOUND'}")

    # Frozen comment bodies (same pattern publish.py preserves on)
    blocks = re.findall(
        r'Show comment for ([\w-]+)\s*</ac:parameter>.*?<!\[CDATA\[(.*?)\]\]>',
        body, re.DOTALL,
    )
    print(f"  comment blocks on page     : {len(blocks)}")
    # sniff any date-looking text in each body so we can eyeball staleness
    date_re = re.compile(r"\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+20\d{2}")
    for key, cbody in blocks[:12]:
        dates = date_re.findall(cbody)
        print(f"    {key:18} dates_in_comment={dates[:4]}")
    if len(blocks) > 12:
        print(f"    ... and {len(blocks) - 12} more")


if __name__ == "__main__":
    main()
