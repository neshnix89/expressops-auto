"""
DISCOVERY PROBE — read-only scratchpad.

PART A — mo_trigger page staleness:
  Compare each comment body frozen on the Confluence page against the FRESH
  body the task just generated into outputs/mo_trigger_{KEY}.txt during the
  last `run`. Same => legitimately unchanged. Different => the page is stale
  because _merge_preserved_bodies froze that container's body.

PART B — MR Status Report recon (C:\\Users\\tmoghanan\\Documents\\AI\\MR Status Report):
  List the folder, print the .py source so we can see it (skips config/secret
  files), and check its Confluence page (560866215) freshness.

All read-only. Guard blocks writes; config secrets are not printed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import load_config
from core.confluence import ConfluenceClient

OUTPUTS = ROOT / "outputs"
MR_DIR = Path(r"C:\Users\tmoghanan\Documents\AI\MR Status Report")
MR_PAGE_ID = 560866215
_SECRETISH = re.compile(r"(config|secret|credential|token|\.env|\.ya?ml|\.ini)", re.I)
# Mask token-like blobs (PATs, base64 keys) so probe output never leaks secrets.
_REDACT = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


def _redact(s: str) -> str:
    return _REDACT.sub("***REDACTED***", s)


def _norm(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _first_diff(a: list[str], b: list[str]) -> str:
    for i in range(max(len(a), len(b))):
        av = a[i] if i < len(a) else "<none>"
        bv = b[i] if i < len(b) else "<none>"
        if av != bv:
            return f"line {i+1}:\n      page : {av!r}\n      fresh: {bv!r}"
    return "(identical)"


def part_a(conf: ConfluenceClient, page_id) -> None:
    print("\n========== PART A: mo_trigger page staleness ==========")
    page = conf.get_page(page_id)
    body = ((page.get("body") or {}).get("storage") or {}).get("value", "") or ""
    blocks = dict(re.findall(
        r'Show comment for ([\w-]+)\s*</ac:parameter>.*?<!\[CDATA\[(.*?)\]\]>',
        body, re.DOTALL,
    ))
    print(f"page version {(page.get('version') or {}).get('number')}, "
          f"{len(blocks)} comment block(s)")
    for key, page_body in blocks.items():
        fresh_file = OUTPUTS / f"mo_trigger_{key}.txt"
        if not fresh_file.exists():
            print(f"  {key:18} NO fresh outputs/ file — can't compare "
                  f"(not regenerated this run?)")
            continue
        fresh = fresh_file.read_text(encoding="utf-8", errors="replace")
        pa, fr = _norm(page_body), _norm(fresh)
        if pa == fr:
            print(f"  {key:18} SAME  — page matches fresh output (not stale)")
        else:
            print(f"  {key:18} STALE — page differs from fresh output")
            print(f"      {_first_diff(pa, fr)}")


def part_b(conf: ConfluenceClient) -> None:
    print("\n========== PART B: MR Status Report recon ==========")
    print(f"folder: {MR_DIR}")
    if not MR_DIR.exists():
        print("  -> folder not found on this machine.")
    else:
        files = sorted(MR_DIR.rglob("*"))
        print(f"  {sum(1 for f in files if f.is_file())} file(s):")
        for f in files:
            if f.is_file():
                rel = f.relative_to(MR_DIR)
                print(f"    {str(rel):45} {f.stat().st_size:>8} bytes")
        # print the .py source so we can analyse it next iteration
        for f in files:
            if f.is_file() and f.suffix == ".py" and not _SECRETISH.search(f.name):
                print(f"\n  ----- {f.relative_to(MR_DIR)} -----")
                txt = f.read_text(encoding="utf-8", errors="replace").split("\n")
                for ln in txt[:150]:
                    print(f"  | {_redact(ln)}")
                if len(txt) > 150:
                    print(f"  | ... ({len(txt) - 150} more lines)")

    print(f"\n  Confluence page {MR_PAGE_ID} freshness:")
    try:
        p = conf.get_page(MR_PAGE_ID)
        v = p.get("version") or {}
        print(f"    title        : {p.get('title')}")
        print(f"    version      : {v.get('number')}")
        print(f"    last updated : {v.get('when')}")
        print(f"    updated by   : {(v.get('by') or {}).get('displayName')}")
    except Exception as e:
        print(f"    could not read page: {type(e).__name__}: {e}")


def main() -> None:
    cfg = load_config(mode_override="live")
    conf = ConfluenceClient(cfg)
    part_a(conf, cfg.pages.get("mo_trigger_comment"))
    part_b(conf)


if __name__ == "__main__":
    main()
