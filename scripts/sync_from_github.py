"""
Sync the company-laptop checkout from GitHub `main` (no git required).

The company laptops have no git installed. This script is the deploy path: it
downloads the latest `main` zip, extracts it, and copies the tree over the
install dir — so deploys run via:

    <python.exe> <install dir>\\scripts\\sync_from_github.py

The install dir is derived from THIS FILE's location, so the same script works
for any user on any laptop (set EXPRESSOPS_HOME to override). It used to be
hardcoded, which meant a second person's install would have targeted the first
person's home directory.

config.yaml (and anything else gitignored) is NOT in the zip, so it is left
untouched. Existing files are overwritten; files not present in the zip are
preserved — so removing a file from the repo does NOT remove it from a laptop.
"""

import io
import os
import shutil
import ssl
import sys
import tempfile
import urllib.request
import zipfile

REPO_ZIP = "https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip"
# Derived from this file's own location (repo_root/scripts/sync_from_github.py),
# so the same script works for any user on any laptop. Hardcoding a path meant a
# second person's install silently targeted the first person's home directory.
# Override with EXPRESSOPS_HOME if the repo lives somewhere unusual.
INSTALL_DIR = os.environ.get(
    "EXPRESSOPS_HOME",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
TOP_LEVEL = "expressops-auto-main"  # zip's root folder


def main():
    print(f"[sync] downloading {REPO_ZIP}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(REPO_ZIP, headers={"User-Agent": "expressops-sync"})
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        raw = resp.read()
    print(f"[sync] downloaded {len(raw)} bytes")

    tmp = tempfile.mkdtemp(prefix="expressops-sync-")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            zf.extractall(tmp)
        src_root = os.path.join(tmp, TOP_LEVEL)
        if not os.path.isdir(src_root):
            # Fall back to whatever single top-level dir the zip contains.
            entries = [d for d in os.listdir(tmp)
                       if os.path.isdir(os.path.join(tmp, d))]
            if len(entries) != 1:
                print(f"[sync] ERROR: unexpected zip layout: {entries}")
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
        print(f"[sync] copied {copied} files into {INSTALL_DIR}")
        print("[sync] OK (config.yaml and other gitignored files left untouched)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
