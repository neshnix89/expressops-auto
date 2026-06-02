"""
DISCOVERY PROBE — read-only scratchpad.

CURRENT PROBE: two jobs.
  (1) Pinpoint the non-ASCII byte breaking load_config (which opens config.yaml
      with the platform default encoding, cp1252, and dies). Reports the
      offending character's location WITHOUT printing any secret values — only
      line number, the key name (left of the colon), and the Unicode name.
  (2) Work around it by loading config as UTF-8, then read the live mo_trigger
      Confluence page (GET only) and report version/last-updated/Run-time/
      frozen comment bodies — the original diagnostic.

All read-only — the guard blocks any write. config.yaml values are never printed.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import CONFIG_PATH, Config
from core.confluence import ConfluenceClient


def _locate_bad_chars() -> bool:
    """Report non-ASCII chars in config.yaml without leaking values. Returns
    True if the file is valid UTF-8."""
    raw = Path(CONFIG_PATH).read_bytes()
    print(f"--- config.yaml encoding check ({len(raw)} bytes) ---")
    try:
        text = raw.decode("utf-8")
        print("  decodes as UTF-8: YES")
    except UnicodeDecodeError as e:
        print(f"  decodes as UTF-8: NO ({e})")
        return False

    found = False
    for lineno, line in enumerate(text.splitlines(), 1):
        for ch in line:
            if ord(ch) > 127:
                found = True
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    where = "<comment line>"
                elif ":" in line:
                    where = f"key '{line.split(':', 1)[0].strip()}' (value not shown)"
                else:
                    where = "<unknown>"
                print(f"  line {lineno}: U+{ord(ch):04X} "
                      f"{unicodedata.name(ch, '?')!r} in {where}")
                break  # one report per line is plenty
    if not found:
        print("  (no non-ASCII characters found)")
    return True


def main() -> None:
    _locate_bad_chars()

    # Verify the REAL load_config now works (utf-8-sig fix). If this no longer
    # raises UnicodeDecodeError, every live task can start again.
    from core.config_loader import load_config
    cfg = load_config(mode_override="live")
    print("\nload_config(): OK  <-- fix confirmed, tasks can start")

    page_id = cfg.pages.get("mo_trigger_comment")
    print(f"\nconfigured page id: {page_id!r}")
    if not page_id:
        print("  -> no page id configured; stopping.")
        return

    conf = ConfluenceClient(cfg)
    page = conf.get_page(page_id)
    ver = page.get("version") or {}
    print("\n--- live page state ---")
    print(f"  title        : {page.get('title')}")
    print(f"  version      : {ver.get('number')}")
    print(f"  last updated : {ver.get('when')}")
    print(f"  updated by   : {(ver.get('by') or {}).get('displayName')}")

    body = ((page.get("body") or {}).get("storage") or {}).get("value", "") or ""
    m = re.search(r"Run time</th>.*?<tr>\s*<td>(.*?)</td>", body, re.DOTALL)
    print(f"  page 'Run time' : {m.group(1).strip() if m else 'NOT FOUND'}")

    blocks = re.findall(
        r'Show comment for ([\w-]+)\s*</ac:parameter>.*?<!\[CDATA\[(.*?)\]\]>',
        body, re.DOTALL,
    )
    print(f"  comment blocks  : {len(blocks)}")
    date_re = re.compile(r"\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+20\d{2}")
    for key, cbody in blocks[:10]:
        print(f"    {key:18} dates={date_re.findall(cbody)[:4]}")


if __name__ == "__main__":
    main()
