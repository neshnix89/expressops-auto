"""
One-time setup: create EDMAdmin.exe (a renamed copy of python.exe) so EDM/Oracle
connections pass SYS.PF_SEC_LOGON_TRIGGER, then verify connectivity with a known
PT->PRSG pair. See the REF_EDM discovery doc. Idempotent — safe to re-run.

Run on the laptop (where the real config.yaml + Python live). Triggered by
setup_edmadmin.bat.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SRC = r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
DEFAULT_DEST = r"C:\Users\tmoghanan\EDMAdmin.exe"
TEST_PT = "PTDE-AXT7"  # REF_EDM confirmed pair: PTDE-AXT7 -> PRSG-A0N5, RELEASESTATE 9


def _load_cfg():
    from core.config_loader import load_config
    return load_config("live")


def ensure_edmadmin(dest: Path) -> bool:
    """Create EDMAdmin.exe at dest by copying python.exe, if not already there."""
    if dest.exists():
        print(f"[setup_edmadmin] already present: {dest}")
        return True
    src = Path(DEFAULT_SRC)
    if not src.exists():
        cand = Path(sys.executable)
        src = cand if cand.name.lower() == "python.exe" else src
    if not src.exists():
        print(f"[setup_edmadmin] ERROR: source python.exe not found ({src})")
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except Exception as e:
        print(f"[setup_edmadmin] ERROR copying {src} -> {dest}: {e}")
        return False
    ok = dest.exists()
    print(f"[setup_edmadmin] copied python.exe -> {dest}  (ok={ok})")
    return ok


def test_edm(cfg) -> None:
    """Run a single known-pair query through EDMClient to confirm EDM works."""
    from core.edm import EDMClient

    client = EDMClient(cfg)
    sql = (
        "SELECT r.REF, r.DOCNUMBER, d.RELEASESTATE "
        "FROM ADMEDP.EDM_REFERENCES r "
        "JOIN ADMEDP.EDM_DOCS d ON d.DOCNUMBER = r.DOCNUMBER "
        "WHERE r.REF = :p0 AND r.DOCNUMBER LIKE 'PRSG-%'"
    )
    print(f"[setup_edmadmin] EDM test query for {TEST_PT} (expect PRSG-A0N5, RELEASESTATE 9) ...")
    try:
        rows = client.query(sql, {"p0": TEST_PT})
    except Exception as e:
        print(f"[setup_edmadmin] EDM TEST FAILED: {e}")
        print("[setup_edmadmin] If this says python3xx.dll / module not found, EDMAdmin.exe "
              "cannot find its runtime — tell Claude and we'll place it beside python312.dll instead.")
        return
    if rows:
        print(f"[setup_edmadmin] ✅ EDM OK — {rows}")
    else:
        print("[setup_edmadmin] EDM connected but returned no rows for the test PT "
              "(connectivity works; the test pair may have changed).")


def main() -> int:
    try:
        cfg = _load_cfg()
        dest = Path(cfg.get("edm.python_exe", DEFAULT_DEST))
    except Exception as e:
        print(f"[setup_edmadmin] WARN: could not load config ({e}); using default path.")
        cfg = None
        dest = Path(DEFAULT_DEST)

    print(f"[setup_edmadmin] target EDMAdmin.exe: {dest}")
    created = ensure_edmadmin(dest)
    if created and cfg is not None:
        test_edm(cfg)
    elif created:
        print("[setup_edmadmin] EDMAdmin.exe ready (config not loaded — skipped EDM test).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
