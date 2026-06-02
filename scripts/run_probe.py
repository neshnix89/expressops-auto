"""
Probe runner — applies the read-only guard, runs scripts/probe.py against live
systems, and tees the output to console + outputs/_probe_latest.txt (plus a
timestamped copy). Invoked by run_probe.bat; not meant to be run by hand.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# IMPORTANT: install the read-only guard BEFORE importing/running any probe.
import scripts.readonly_guard  # noqa: E402,F401

OUTPUTS = ROOT / "outputs"


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            st.write(s)

    def flush(self):
        for st in self._streams:
            st.flush()


def main() -> int:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest = OUTPUTS / "_probe_latest.txt"

    rc = 0
    console = sys.__stdout__
    with open(latest, "w", encoding="utf-8") as fh:
        tee = _Tee(console, fh)
        old_stdout = sys.stdout
        sys.stdout = tee
        try:
            print(f"=== probe run {stamp}  (READ-ONLY) ===")
            import scripts.probe as probe
            importlib.reload(probe)  # pick up the freshly-synced probe each run
            probe.main()
        except Exception:
            rc = 1
            print("\n[probe ERROR]")
            traceback.print_exc(file=tee)
        finally:
            print(f"\n=== done  rc={rc} ===")
            sys.stdout = old_stdout

    # keep a timestamped history copy alongside the stable latest file
    shutil.copy2(latest, OUTPUTS / f"probe_{stamp}.txt")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
