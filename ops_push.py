"""
ops_push.py — Push files from company laptop to GitHub via API.
No Git required. Uses Python requests (handles corporate proxy).

Usage: python ops_push.py "commit message"
"""
import os
import sys
import json
import base64
import requests

REPO_OWNER = "neshnix89"
REPO_NAME = "expressops-auto"
BRANCH = "main"
LOCAL_ROOT = r"C:\Users\tmoghanan\Documents\AI\expressops-auto"
CONFIG_FILE = os.path.join(LOCAL_ROOT, ".ops_config")

# Files/folders to skip
SKIP_DIRS = {".ops_config", "__pycache__", ".git", "mock_data"}
SKIP_EXT = {".pyc", ".db", ".tmp", ".png", ".xml"}
SKIP_PREFIXES = ("debug_", "result_", "discover_", "phase_b_", "import pyodbc")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def should_skip(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        if part in SKIP_DIRS:
            return True
    fname = os.path.basename(rel_path)
    if fname.startswith(SKIP_PREFIXES):
        return True
    if fname in (".ops_config", "edge_cookies_copy.db"):
        return True
    _, ext = os.path.splitext(rel_path)
    if ext in SKIP_EXT:
        return True
    return False


def get_token():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return f.read().strip()
    token = input("Paste your GitHub token (ghp_...): ").strip()
    with open(CONFIG_FILE, "w") as f:
        f.write(token)
    print(f"Token saved to {CONFIG_FILE}")
    return token


def main():
    message = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "sync from company laptop"

    token = get_token()
    api = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    print(f"\n=== Pushing to {REPO_OWNER}/{REPO_NAME} ({BRANCH}) ===")
    print(f"Message: {message}\n")

    # Get current commit
    r = requests.get(f"{api}/git/ref/heads/{BRANCH}", headers=headers)
    r.raise_for_status()
    commit_sha = r.json()["object"]["sha"]

    r = requests.get(f"{api}/git/commits/{commit_sha}", headers=headers)
    r.raise_for_status()
    tree_sha = r.json()["tree"]["sha"]
    print(f"Current commit: {commit_sha[:8]}")

    # Scan files
    files = []
    for root, dirs, filenames in os.walk(LOCAL_ROOT):
        # Skip hidden/unwanted dirs
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "mock_data")]
        for fname in filenames:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, LOCAL_ROOT).replace("\\", "/")
            if not should_skip(rel):
                files.append((full, rel))

    print(f"Files to push: {len(files)}")

    # Create blobs
    tree_items = []
    skipped = []
    for i, (full, rel) in enumerate(files):
        fsize = os.path.getsize(full)
        if fsize > MAX_FILE_SIZE:
            print(f"  [{i+1}/{len(files)}] SKIP (too large {fsize//1024}KB): {rel}")
            skipped.append(rel)
            continue
        if fsize == 0:
            print(f"  [{i+1}/{len(files)}] SKIP (empty): {rel}")
            skipped.append(rel)
            continue

        try:
            with open(full, "rb") as f:
                content = base64.b64encode(f.read()).decode()

            r = requests.post(f"{api}/git/blobs", headers=headers,
                             json={"content": content, "encoding": "base64"})
            r.raise_for_status()

            tree_items.append({
                "path": rel,
                "mode": "100644",
                "type": "blob",
                "sha": r.json()["sha"],
            })
            print(f"  [{i+1}/{len(files)}] {rel}")
        except Exception as e:
            print(f"  [{i+1}/{len(files)}] SKIP (error): {rel} — {e}")
            skipped.append(rel)

    if skipped:
        print(f"\nSkipped {len(skipped)} files")
    if not tree_items:
        print("No files to push!")
        return

    # Create tree
    print("\nCreating tree...")
    r = requests.post(f"{api}/git/trees", headers=headers,
                     json={"base_tree": tree_sha, "tree": tree_items})
    r.raise_for_status()
    new_tree = r.json()["sha"]

    # Create commit
    print("Creating commit...")
    r = requests.post(f"{api}/git/commits", headers=headers,
                     json={"message": message, "tree": new_tree, "parents": [commit_sha]})
    r.raise_for_status()
    new_commit = r.json()["sha"]

    # Update branch
    r = requests.patch(f"{api}/git/refs/heads/{BRANCH}", headers=headers,
                      json={"sha": new_commit})
    r.raise_for_status()

    print(f"\n=== Pushed! Commit: {new_commit[:8]} ===")
    print(f"View: https://github.com/{REPO_OWNER}/{REPO_NAME}")


if __name__ == "__main__":
    main()
