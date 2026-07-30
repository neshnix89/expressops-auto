"""
Sync a SEPARATE dev/staging checkout from a GitHub *feature branch* (no git).

Twin of scripts/sync_from_github.py, but it pulls the feature branch zip and
writes into ...\\AI\\expressops-auto-dev — so the production `main` checkout at
...\\AI\\expressops-auto is never touched. The dev folder stays under
C:\\Users\\tmoghanan\\Documents\\AI\\, so scripts there are still inside the
Relay whitelist (python C:\\Users\\tmoghanan\\Documents\\AI\\...).

Run:
    python C:\\Users\\tmoghanan\\Documents\\AI\\expressops-auto-dev\\scripts\\sync_dev_from_github.py

config.yaml and anything gitignored are NOT in the zip, so they're left
untouched. Existing files are overwritten; files not in the zip are preserved.

To point this at a different feature branch later, edit BRANCH below.
"""

import io
import os
import shutil
import ssl
import sys
import tempfile
import urllib.request
import zipfile

# --- Edit this when the feature branch changes -----------------------------
BRANCH = "claude/m3-ref-order-jira-monitor-4vmkre"
INSTALL_DIR = r"C:\Users\tmoghanan\Documents\AI\expressops-auto-dev"
# ---------------------------------------------------------------------------

REPO_ZIP = (
    "https://github.com/neshnix89/expressops-auto/archive/refs/heads/"
    f"{BRANCH}.zip"
)


def main():
    print(f"[sync-dev] branch: {BRANCH}")
    print(f"[sync-dev] downloading {REPO_ZIP}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(REPO_ZIP, headers={"User-Agent": "expressops-sync"})
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        raw = resp.read()
    print(f"[sync-dev] downloaded {len(raw)} bytes")

    tmp = tempfile.mkdtemp(prefix="expressops-sync-dev-")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            zf.extractall(tmp)
        # GitHub zip always has exactly one top-level dir; branch slashes
        # become dashes in its name, so detect it rather than hardcode.
        entries = [d for d in os.listdir(tmp)
                   if os.path.isdir(os.path.join(tmp, d))]
        if len(entries) != 1:
            print(f"[sync-dev] ERROR: unexpected zip layout: {entries}")
            sys.exit(1)
        src_root = os.path.join(tmp, entries[0])

        copied = 0
        for dirpath, _dirnames, filenames in os.walk(src_root):
            rel = os.path.relpath(dirpath, src_root)
            dest_dir = INSTALL_DIR if rel == "." else os.path.join(INSTALL_DIR, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for fn in filenames:
                shutil.copy2(os.path.join(dirpath, fn), os.path.join(dest_dir, fn))
                copied += 1
        print(f"[sync-dev] copied {copied} files into {INSTALL_DIR}")
        print("[sync-dev] OK (config.yaml and other gitignored files left untouched)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
