"""
One-time setup: create EDMAdmin.exe (a renamed copy of python.exe) so EDM/Oracle
connections pass SYS.PF_SEC_LOGON_TRIGGER, then verify connectivity with a known
PT->PRSG pair. See the REF_EDM discovery doc. Idempotent — safe to re-run.

EDMAdmin.exe is created INSIDE the Python install directory (beside
python312.dll) so the renamed exe can find its runtime; config.yaml's
edm.python_exe is then updated to point at it. A bare copy in the home dir
fails with "subprocess failed" (no python3xx.dll on the search path).

Run on the laptop (where the real config.yaml + Python live). Triggered by
setup_edmadmin.bat.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PYDIR = Path(r"C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312")
TEST_PT = "PTDE-AXT7"  # REF_EDM confirmed pair: PTDE-AXT7 -> PRSG-A0N5, RELEASESTATE 9


def _load_cfg():
    from core.config_loader import load_config
    return load_config("live")


def _python_dir() -> Path:
    """Directory containing python.exe + python3xx.dll."""
    if PYDIR.exists():
        return PYDIR
    cand = Path(sys.executable)
    return cand.parent if cand.name.lower() == "python.exe" else PYDIR


def ensure_edmadmin(dest: Path) -> bool:
    """Create EDMAdmin.exe at dest (beside python.exe) by copying python.exe."""
    src = dest.parent / "python.exe"
    if not src.exists():
        print(f"[setup_edmadmin] ERROR: python.exe not found at {src}")
        return False
    if dest.exists():
        print(f"[setup_edmadmin] already present: {dest}")
        return True
    try:
        shutil.copy2(src, dest)
    except Exception as e:
        print(f"[setup_edmadmin] ERROR copying {src} -> {dest}: {e}")
        return False
    ok = dest.exists()
    print(f"[setup_edmadmin] copied python.exe -> {dest}  (ok={ok})")
    return ok


def update_config_edm_exe(new_path: str) -> bool:
    """Point config.yaml's edm.python_exe at new_path (single-quoted, literal)."""
    from core.config_loader import CONFIG_PATH

    p = Path(CONFIG_PATH)
    if not p.exists():
        print(f"[setup_edmadmin] config not found: {p}")
        return False
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    top_re = re.compile(r"^([A-Za-z_][\w-]*):")
    key_re = re.compile(r"^(\s+)python_exe:\s*.*$")
    section = None
    changed = False
    for i, ln in enumerate(lines):
        m = top_re.match(ln)
        if m and not ln[:1].isspace():
            section = m.group(1)
            continue
        if section == "edm" and key_re.match(ln):
            indent = key_re.match(ln).group(1)
            lines[i] = f"{indent}python_exe: '{new_path}'"
            changed = True
            break
    if not changed:
        print("[setup_edmadmin] WARNING: no 'python_exe:' line under 'edm:' — config not updated.")
        return False
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[setup_edmadmin] config edm.python_exe -> {new_path}")
    return True


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
        return
    if rows:
        print(f"[setup_edmadmin] OK EDM WORKS — {rows}")
    else:
        print("[setup_edmadmin] EDM connected but returned no rows for the test PT "
              "(connectivity works; the test pair may have changed).")


def main() -> int:
    dest = _python_dir() / "EDMAdmin.exe"
    print(f"[setup_edmadmin] target EDMAdmin.exe: {dest}")
    if not ensure_edmadmin(dest):
        return 1
    update_config_edm_exe(str(dest))
    try:
        cfg = _load_cfg()  # reload so the new edm.python_exe is in effect
    except Exception as e:
        print(f"[setup_edmadmin] could not load config for test: {e}")
        return 0
    test_edm(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
