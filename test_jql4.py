import sys
sys.path.insert(0, ".")
from core.config_loader import load_config
from core.jira_client import JiraClient

config = load_config(mode_override="live")
jira = JiraClient(config)

jql = 'issuetype = "Work Container" AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" AND resolution is EMPTY ORDER BY created ASC'
result = jira.search(jql, fields=["summary"], max_results=5)
print(f"Found {result['total']} containers")
for i in result.get("issues", [])[:5]:
    print(f"  {i['key']}: {i['fields']['summary'][:70]}")
