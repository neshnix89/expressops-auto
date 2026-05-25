"""
Discovery script for container_summary task.
Run on company laptop to fetch child Work Packages as mock data.

Usage:
    cd C:\\Users\\tmoghanan\\Documents\\AI\\expressops-auto
    C:\\Users\\tmoghanan\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tasks\\container_summary\\discover.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.config_loader import load_config
from core.jira_client import JiraClient

MOCK_DIR = Path(__file__).parent / "mock_data"
MOCK_DIR.mkdir(parents=True, exist_ok=True)

TEST_KEYS = [
    "NPIOTHER-3902",
    "LCUSAMB-1755",
    "NPIOTHER-4085",
]

CHILD_FIELDS = ["summary", "status", "resolution", "assignee"]


def fetch_children(jira: JiraClient, parent_key: str) -> list:
    """
    Fetch child Work Packages using the correct relation() JQL.
    jira_client.get_children() uses bare relation() which returns HTTP 400.
    This uses the full syntax confirmed working in JIRA.
    """
    jql = f'issue in relation("{parent_key}", "Project Children", Tasks, Deviations, level1)'
    return jira.search_all(jql, fields=CHILD_FIELDS)


def main() -> int:
    config = load_config(mode_override="live")
    jira = JiraClient(config)

    print("Verifying JIRA connection...")
    try:
        myself = jira._request("GET", f"{jira.base_url}/rest/api/2/myself").json()
        print(f"Authenticated as: {myself.get('displayName', '?')} ({myself.get('name', '?')})\n")
    except Exception as e:
        print(f"JIRA auth failed: {e}")
        return 1

    for key in TEST_KEYS:
        # Skip issue fetch if already exists
        issue_path = MOCK_DIR / f"issue_{key}.json"
        if issue_path.exists():
            print(f"  issue_{key}.json already exists, skipping issue fetch")
        else:
            print(f"\n  Fetching issue {key}...")
            try:
                issue = jira.get_issue(key, expand="renderedFields")
                with open(issue_path, "w", encoding="utf-8") as f:
                    json.dump(issue, f, indent=2, default=str)
                print(f"  Saved: issue_{key}.json")
            except Exception as e:
                print(f"  ERROR fetching issue: {e}")

        # Fetch children
        print(f"\n  Fetching children for {key}...")
        try:
            children = fetch_children(jira, key)
            print(f"  Found {len(children)} children:")
            for wp in children:
                s = (wp.get("fields", {}).get("summary") or "?").strip()
                st = (wp.get("fields", {}).get("status") or {}).get("name", "?")
                res = ((wp.get("fields", {}).get("resolution") or {}).get("name") or "")
                asgn = ((wp.get("fields", {}).get("assignee") or {}).get("displayName") or "unassigned")
                print(f"    {wp['key']:20s} {st:15s} {res:12s} {asgn:20s} {s}")

            child_path = MOCK_DIR / f"children_{key}.json"
            with open(child_path, "w", encoding="utf-8") as f:
                json.dump(children, f, indent=2, default=str)
            print(f"  Saved: children_{key}.json")
        except Exception as e:
            print(f"  ERROR fetching children: {e}")

    print(f"\n{'='*60}")
    print(f"  Discovery complete. Files in: {MOCK_DIR}")
    print(f"  Next: git add/commit/push, then ops sync on VPS")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
