---
name: jira
description: Manage Jira issues — create, update, search, and comment.
---

# Jira

Use the Jira tools to manage issues on your Jira board.

## Available Tools

| Tool | Purpose |
|------|---------|
| `jira_create_issue` | Create a new issue |
| `jira_list_issues` | List issues with filters |
| `jira_get_issue` | Get full issue details |
| `jira_update_issue` | Update fields or transition status |
| `jira_add_comment` | Add a comment to an issue |
| `jira_search` | Raw JQL search |

## Examples

Create a task:
```
jira_create_issue(summary="Set up CI pipeline", description="Configure GitHub Actions for the monorepo", issue_type="Task")
```

List in-progress work:
```
jira_list_issues(status="In Progress")
```

Move an issue to Done:
```
jira_update_issue(issue_key="DEVPRO-42", transition="Done")
```

Add a comment:
```
jira_add_comment(issue_key="DEVPRO-42", body="Deployed to staging, awaiting QA.")
```

Search with JQL:
```
jira_search(jql="project = DEVPRO AND assignee = currentUser() AND status != Done ORDER BY priority DESC")
```

## Tips

- The default project is configured in `integrations.jira.defaultProject`. You can omit the `project` parameter for create/list if a default is set.
- Use `jira_list_issues` for simple filters and `jira_search` for complex JQL queries.
- Status transitions must match the workflow — use the exact transition name (e.g. "In Progress", "Done", "To Do").
