import sys, json
sys.path.insert(0, ".")
from core.config_loader import load_config
from core.jira_client import JiraClient
from tasks.container_summary.main import CONTAINERS_JQL

config = load_config(mode_override="live")
jira = JiraClient(config)

# Send raw request to see full error response
resp = jira.session.post(
    f"{jira.base_url}/rest/api/2/search",
    json={"jql": CONTAINERS_JQL, "maxResults": 5, "fields": ["summary"]}
)
print(f"Status: {resp.status_code}")
print(f"Body: {resp.text[:1000]}")
