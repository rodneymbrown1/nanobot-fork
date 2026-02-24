---
name: notion
description: Manage Notion pages and databases — create, search, update, and query.
---

# Notion

Use the Notion tools to manage pages and databases in your Notion workspace.

## Available Tools

| Tool | Purpose |
|------|---------|
| `notion_create_page` | Create a page under a parent |
| `notion_get_page` | Read page properties and content |
| `notion_update_page` | Update title, append content, or archive |
| `notion_search` | Search pages and databases by query |
| `notion_create_database_entry` | Add a row to a database |
| `notion_query_database` | Query a database with filters/sorts |

## Examples

Create a strategy page:
```
notion_create_page(title="Q2 Sprint Planning", content="Goals:\n- Ship v2 API\n- Onboard 3 clients")
```

Search for a page:
```
notion_search(query="sprint planning")
```

Update a page:
```
notion_update_page(page_id="abc123", append_content="Update: milestone hit on schedule.")
```

Add a database entry:
```
notion_create_database_entry(database_id="def456", properties={"Name": "Client Onboarding", "Status": {"select": {"name": "In Progress"}}})
```

Query a database:
```
notion_query_database(database_id="def456", filter={"property": "Status", "select": {"equals": "In Progress"}})
```

## Tips

- The default parent page is configured in `integrations.notion.rootPageId`. You can omit `parent_page_id` when creating pages if a default is set.
- For database entries, simple string values are auto-converted to `rich_text`. For `title`, `select`, `date`, etc., use the Notion API property format.
- Use `notion_search` for quick lookups and `notion_query_database` for structured filtering on a specific database.
