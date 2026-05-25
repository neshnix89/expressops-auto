import sys
sys.path.insert(0, ".")
from core.config_loader import load_config
from core.jira_client import JiraClient
from tasks.container_summary.main import CONTAINERS_JQL

config = load_config(mode_override="live")
jira = JiraClient(config)

myself = jira._request("GET", f"{jira.base_url}/rest/api/2/myself").json()
print(f"Auth OK: {myself['displayName']}")

try:
    result = jira.search(CONTAINERS_JQL, fields=["summary"], max_results=5)
    print(f"JQL OK: {result['total']} containers found")
except Exception as e:
    print(f"JQL failed: {e}")
