import sys
sys.path.insert(0, ".")
from core.config_loader import load_config
from core.jira_client import JiraClient
from tasks.container_summary.main import CONTAINERS_JQL

config = load_config(mode_override="live")
jira = JiraClient(config)

# Test GET (URL-encoded) instead of POST (JSON body)
resp = jira.session.get(
    f"{jira.base_url}/rest/api/2/search",
    params={"jql": CONTAINERS_JQL, "maxResults": 5, "fields": "summary"}
)
print(f"GET status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    print(f"Found {data['total']} containers")
    for i in data.get("issues", [])[:3]:
        print(f"  {i['key']}: {i['fields']['summary'][:60]}")
else:
    print(f"GET failed: {resp.text[:500]}")
