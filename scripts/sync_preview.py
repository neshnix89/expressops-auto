"""
Show what `sync_now.bat` WOULD change on this machine — without changing anything.

    python scripts\\sync_preview.py                 # summary
    python scripts\\sync_preview.py --detail        # every file, grouped
    python scripts\\sync_preview.py --diff core/m3.py   # the actual line diff

`sync_from_github.py` overwrites files from the GitHub `main` zip and never
deletes, so the only questions before a sync are "which of my files get
overwritten, and by what". Nothing on a laptop records which commit it is on,
so the honest way to answer that is to fetch the zip and compare it file by
file against what is actually on disk. That is all this does.

Three categories, and the distinction matters:

  MODIFIED   the file exists here with different content — this is the real
             blast radius of a sync
  NEW        not here yet; nothing of yours is overwritten
  LOCAL-ONLY here but not in the repo — config.yaml, EDMAdmin.exe, outputs,
             logs. Sync never deletes, so every one of these survives. Listed
             so you can see that for yourself rather than take it on trust.

Read-only: it downloads the zip to a temp folder, reads it, and deletes it.
Nothing under the install directory is written except the report, and even that
only with --report.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import os
import ssl
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ZIP = "https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip"
TOP_LEVEL = "expressops-auto-main"

INSTALL_DIR = Path(os.environ.get("EXPRESSOPS_HOME", str(PROJECT_ROOT)))

# Only what git can never hold. NOT logs/ or outputs/: .gitignore lists them,
# but files committed before that rule was added stay tracked, so some of them
# ARE in the zip and a sync does overwrite them. Skipping them here would hide
# exactly the surprise this tool exists to surface.
IGNORED_DIRS = {".git", "__pycache__", ".venv", "venv", ".vscode", ".idea",
                ".pytest_cache", ".mypy_cache"}

# Files whose content decides what the automation DOES, as opposed to what it
# says. A sync that only touches docs is a different conversation from one that
# touches these.
def is_behaviour(rel: str) -> bool:
    low = rel.replace("\\", "/").lower()
    if low.startswith("docs/") or low.endswith(".md"):
        return False
    return low.endswith((".py", ".bat", ".ps1", ".yaml", ".yml"))


def canon(data: bytes) -> bytes:
    """Fold CRLF to LF for text so line endings alone never read as a change.

    A Windows checkout with core.autocrlf=true holds CRLF; the GitHub zip holds
    LF. Comparing raw bytes reports every single text file as modified, which is
    both wrong and useless. Binary files are compared byte for byte.
    """
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(canon(data)).hexdigest()


def fetch_zip() -> dict[str, bytes]:
    print(f"[preview] downloading {REPO_ZIP}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(REPO_ZIP, headers={"User-Agent": "expressops-sync"})
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        raw = resp.read()
    print(f"[preview] downloaded {len(raw)} bytes")

    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        root = TOP_LEVEL + "/"
        if not any(n.startswith(root) for n in names):
            tops = {n.split("/", 1)[0] for n in names}
            if len(tops) != 1:
                print(f"[preview] ERROR: unexpected zip layout: {sorted(tops)}")
                sys.exit(1)
            root = tops.pop() + "/"
        for name in names:
            if name.endswith("/") or not name.startswith(root):
                continue
            files[name[len(root):]] = zf.read(name)
    return files


def local_files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(INSTALL_DIR):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = str(full.relative_to(INSTALL_DIR)).replace("\\", "/")
            out[rel] = full
    return out


def classify(remote: dict[str, bytes], local: dict[str, Path]):
    modified, new, unchanged, unreadable = [], [], [], []
    for rel, data in sorted(remote.items()):
        path = local.get(rel)
        if path is None:
            new.append(rel)
            continue
        try:
            current = path.read_bytes()
        except OSError as exc:
            # Usually a path over Windows' 260-char limit. Not knowing is a
            # different answer from "this will be overwritten", so keep it apart.
            unreadable.append((rel, str(exc)))
            continue
        if sha(current) == sha(data):
            unchanged.append(rel)
        else:
            # Line counts say more than byte counts for source files.
            try:
                a = canon(current).decode("utf-8", "replace").splitlines()
                b = canon(data).decode("utf-8", "replace").splitlines()
                delta = len(b) - len(a)
                detail = f"{len(a)} -> {len(b)} lines ({delta:+d})"
            except Exception:  # noqa: BLE001
                detail = f"{len(current)} -> {len(data)} bytes"
            modified.append((rel, detail))
    local_only = sorted(set(local) - set(remote))
    return modified, new, unchanged, local_only, unreadable


def show_diff(remote: dict[str, bytes], target: str) -> int:
    key = target.replace("\\", "/")
    if key not in remote:
        print(f"[preview] '{key}' is not in the repo zip.")
        near = difflib.get_close_matches(key, list(remote), n=5)
        if near:
            print("  did you mean: " + ", ".join(near))
        return 1
    path = INSTALL_DIR / key
    if not path.exists():
        print(f"[preview] '{key}' is NEW — it does not exist on this machine yet.")
        return 0
    a = canon(path.read_bytes()).decode("utf-8", "replace").splitlines(keepends=True)
    b = canon(remote[key]).decode("utf-8", "replace").splitlines(keepends=True)
    diff = list(difflib.unified_diff(a, b, fromfile=f"LOCAL/{key}",
                                     tofile=f"GITHUB/{key}"))
    if not diff:
        print(f"[preview] '{key}' is already identical.")
        return 0
    sys.stdout.writelines(diff)
    return 0


def run(detail: bool, diff_target: str | None, report: bool) -> int:
    print(f"[preview] install dir: {INSTALL_DIR}")
    remote = fetch_zip()

    if diff_target:
        return show_diff(remote, diff_target)

    local = local_files()
    modified, new, unchanged, local_only, unreadable = classify(remote, local)

    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    out("")
    out("=" * 72)
    out(f"SYNC PREVIEW — {datetime.now():%Y-%m-%d %H:%M:%S}")
    out("=" * 72)
    out(f"  files in GitHub main : {len(remote)}")
    out(f"  already identical    : {len(unchanged)}")
    out(f"  WOULD BE OVERWRITTEN : {len(modified)}")
    out(f"  would be added (new) : {len(new)}")
    out(f"  local-only (kept)    : {len(local_only)}")

    behaviour = [(r, d) for r, d in modified if is_behaviour(r)]
    docs = [(r, d) for r, d in modified if not is_behaviour(r)]

    out("")
    out(f"OVERWRITTEN — code / config / runners  ({len(behaviour)})")
    if not behaviour:
        out("  (none — a sync would not change any executable file)")
    for rel, det in behaviour:
        out(f"  M  {rel:<52} {det}")
        out(f"       see it: python scripts\\sync_preview.py --diff {rel}")

    out("")
    out(f"OVERWRITTEN — docs / mock fixtures / other  ({len(docs)})")
    for rel, det in docs:
        out(f"  M  {rel:<52} {det}")

    if unreadable:
        out("")
        out(f"COULD NOT BE CHECKED  ({len(unreadable)})")
        out("  (almost always a path over Windows' 260-character limit)")
        for rel, why in unreadable:
            out(f"  ?  {rel}")
            out(f"       {why}")

    out("")
    out(f"NEW — nothing of yours is replaced  ({len(new)})")
    if detail:
        for rel in new:
            out(f"  +  {rel}")
    else:
        for rel in new[:20]:
            out(f"  +  {rel}")
        if len(new) > 20:
            out(f"  ... and {len(new) - 20} more (--detail to list them)")

    out("")
    out(f"LOCAL-ONLY — not in the repo, and sync never deletes  ({len(local_only)})")
    for rel in local_only[:40]:
        out(f"  .  {rel}")
    if len(local_only) > 40:
        out(f"  ... and {len(local_only) - 40} more")
    out("")
    out("  .git/, __pycache__/ and virtualenvs are skipped by this listing.")
    out("  logs/ and outputs/ are NOT: .gitignore lists them, but files committed")
    out("  before that rule was added are still tracked, so some of them are in")
    out("  the zip and a sync overwrites them. If any appear above under")
    out("  OVERWRITTEN, that is real — old committed copies replacing yours.")

    out("")
    out("=" * 72)
    if not modified and not new:
        out("Already identical to GitHub main. A sync would change nothing.")
    elif behaviour:
        out("Read the code/config list above before syncing. Everything else is")
        out("additive or documentation.")
    else:
        out("No executable file would change. This sync is docs and new files only.")
    out("Nothing has been modified by this preview.")

    if report:
        dest = INSTALL_DIR / "outputs" / "sync_preview.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nReport written: {dest}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Dry-run of sync_now.bat — what would change, and nothing else")
    p.add_argument("--detail", action="store_true", help="list every new file")
    p.add_argument("--diff", metavar="PATH",
                   help="print the line diff for one file, e.g. core/m3.py")
    p.add_argument("--report", action="store_true",
                   help="also write outputs/sync_preview.txt")
    args = p.parse_args()
    return run(args.detail, args.diff, args.report)


if __name__ == "__main__":
    sys.exit(main())
