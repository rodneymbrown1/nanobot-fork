"""Notion tools — full API coverage for pages, databases, blocks, and search."""

from __future__ import annotations

import json
from typing import Any

import httpx

from core.agent.tools.base import Tool

NOTION_VERSION = "2022-06-28"


class _NotionClient:
    """Shared async HTTP helper for the Notion API."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.notion.com/v1{path}",
                headers=self._headers(),
                params=params,
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json()

    async def post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.notion.com/v1{path}",
                headers=self._headers(),
                json=body,
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json()

    async def patch(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"https://api.notion.com/v1{path}",
                headers=self._headers(),
                json=body,
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json()

    async def delete(self, path: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"https://api.notion.com/v1{path}",
                headers=self._headers(),
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json() if r.content else {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rich_text(text: str) -> list[dict]:
    """Wrap a plain string as Notion rich_text array."""
    return [{"type": "text", "text": {"content": text}}]


def _text_to_blocks(text: str) -> list[dict]:
    """Convert plain text to Notion paragraph blocks."""
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(line)},
        }
        for line in text.split("\n")
        if line.strip()
    ]


def _extract_title(obj: dict) -> str:
    """Extract the plain-text title from a Notion page or database."""
    # Database title is a top-level array
    if isinstance(obj.get("title"), list):
        return "".join(p.get("plain_text", "") for p in obj["title"])
    # Page title is inside properties
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(p.get("plain_text", "") for p in prop.get("title", []))
    return ""


def _format_page(page: dict) -> dict:
    """Extract key fields from a Notion page for LLM consumption."""
    return {
        "id": page.get("id"),
        "title": _extract_title(page),
        "url": page.get("url"),
        "created_time": page.get("created_time"),
        "last_edited_time": page.get("last_edited_time"),
        "archived": page.get("archived"),
    }


def _format_db(db: dict) -> dict:
    """Extract key fields from a Notion database for LLM consumption."""
    props = {}
    for name, prop in db.get("properties", {}).items():
        info: dict[str, Any] = {"type": prop.get("type")}
        # Include options for select/multi_select/status so the agent knows valid values
        for key in ("select", "multi_select", "status"):
            if key in prop:
                opts = prop[key].get("options", [])
                info["options"] = [o.get("name") for o in opts]
                if key == "status":
                    info["groups"] = [
                        {"name": g.get("name"), "option_ids": g.get("option_ids", [])}
                        for g in prop[key].get("groups", [])
                    ]
        props[name] = info
    return {
        "id": db.get("id"),
        "object": "database",
        "title": _extract_title(db),
        "url": db.get("url"),
        "properties": props,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class NotionCreatePageTool(Tool):
    """Create a Notion page."""

    name = "notion_create_page"
    description = "Create a new Notion page under a parent page."
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Page title"},
            "content": {"type": "string", "description": "Page body content (plain text)"},
            "parent_page_id": {
                "type": "string",
                "description": "Parent page ID (uses default root if omitted)",
            },
        },
        "required": ["title"],
    }

    def __init__(self, client: _NotionClient, root_page_id: str = ""):
        self._client = client
        self._root_page_id = root_page_id

    async def execute(self, **kwargs: Any) -> str:
        parent_id = kwargs.get("parent_page_id") or self._root_page_id
        if not parent_id:
            return "Error: No parent page ID specified and no default root_page_id configured."

        # Auto-detect parent type
        try:
            await self._client.get(f"/pages/{parent_id}")
            parent = {"page_id": parent_id}
        except httpx.HTTPStatusError:
            parent = {"database_id": parent_id}

        body: dict[str, Any] = {
            "parent": parent,
            "properties": {
                "title": {"title": _rich_text(kwargs["title"])}
            },
        }
        if content := kwargs.get("content"):
            body["children"] = _text_to_blocks(content)

        try:
            result = await self._client.post("/pages", body)
            return json.dumps(_format_page(result))
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionGetPageTool(Tool):
    """Read a Notion page or database by ID."""

    name = "notion_get_page"
    description = "Get a Notion page or database by ID. Returns properties, content blocks, and metadata."
    parameters = {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "Notion page or database ID"},
        },
        "required": ["page_id"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        obj_id = kwargs["page_id"]
        try:
            page = await self._client.get(f"/pages/{obj_id}")
            blocks = await self._client.get(f"/blocks/{obj_id}/children")
            result = _format_page(page)
            result["blocks"] = blocks.get("results", [])
            return json.dumps(result, default=str)
        except httpx.HTTPStatusError:
            try:
                db = await self._client.get(f"/databases/{obj_id}")
                return json.dumps(_format_db(db), default=str)
            except httpx.HTTPStatusError as e:
                return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionUpdatePageTool(Tool):
    """Update a Notion page's properties or database entry properties."""

    name = "notion_update_page"
    description = (
        "Update a Notion page or database entry. Can change title, set property values "
        "(status, select, date, people, etc.), append content, or archive."
    )
    parameters = {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "Notion page or database entry ID"},
            "title": {"type": "string", "description": "New title"},
            "properties": {
                "type": "object",
                "description": (
                    "Property updates as {name: value}. Use Notion API format:\n"
                    "- status: {status: {name: 'In Progress'}}\n"
                    "- select: {select: {name: 'High'}}\n"
                    "- multi_select: {multi_select: [{name: 'Tag1'}]}\n"
                    "- date: {date: {start: '2025-03-01'}}\n"
                    "- checkbox: {checkbox: true}\n"
                    "- number: {number: 42}\n"
                    "- url: {url: 'https://...'}\n"
                    "- rich_text: {rich_text: [{type: 'text', text: {content: '...'}}]}\n"
                    "- people: {people: [{id: 'user-id'}]}"
                ),
            },
            "append_content": {"type": "string", "description": "Text to append as new blocks"},
            "archived": {"type": "boolean", "description": "Set to true to archive the page"},
        },
        "required": ["page_id"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        page_id = kwargs["page_id"]
        results = []

        try:
            patch_body: dict[str, Any] = {}

            # Title update
            if title := kwargs.get("title"):
                patch_body.setdefault("properties", {})["title"] = {
                    "title": _rich_text(title)
                }

            # Arbitrary property updates
            if props := kwargs.get("properties"):
                patch_body.setdefault("properties", {}).update(props)

            if kwargs.get("archived") is not None:
                patch_body["archived"] = kwargs["archived"]

            if patch_body:
                await self._client.patch(f"/pages/{page_id}", patch_body)
                results.append("Page updated.")

            # Append content blocks
            if content := kwargs.get("append_content"):
                blocks = _text_to_blocks(content)
                await self._client.patch(
                    f"/blocks/{page_id}/children", {"children": blocks}
                )
                results.append(f"Appended {len(blocks)} blocks.")

            return " ".join(results) if results else "No changes specified."
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionDeleteBlockTool(Tool):
    """Delete (archive) a Notion block, page, or database entry."""

    name = "notion_delete_block"
    description = "Delete a Notion block, page, or database entry by ID. This archives the item."
    parameters = {
        "type": "object",
        "properties": {
            "block_id": {
                "type": "string",
                "description": "ID of the block, page, or database entry to delete",
            },
        },
        "required": ["block_id"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        block_id = kwargs["block_id"]
        try:
            await self._client.delete(f"/blocks/{block_id}")
            return f"Deleted block {block_id}."
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


# ---------------------------------------------------------------------------
# Blocks — rich content
# ---------------------------------------------------------------------------

class NotionAppendBlocksTool(Tool):
    """Append rich content blocks to a page."""

    name = "notion_append_blocks"
    description = (
        "Append rich content blocks to a Notion page or block. Supports headings, "
        "to-do lists, bulleted/numbered lists, toggles, callouts, code blocks, "
        "dividers, quotes, and more."
    )
    parameters = {
        "type": "object",
        "properties": {
            "parent_id": {"type": "string", "description": "Page or block ID to append to"},
            "blocks": {
                "type": "array",
                "description": (
                    "Array of block objects. Each block has a type and content. Examples:\n"
                    "- Heading: {type: 'heading_1', heading_1: {rich_text: [{type: 'text', text: {content: 'Title'}}]}}\n"
                    "- To-do: {type: 'to_do', to_do: {rich_text: [{type: 'text', text: {content: 'Task'}}], checked: false}}\n"
                    "- Bullet: {type: 'bulleted_list_item', bulleted_list_item: {rich_text: [{type: 'text', text: {content: 'Item'}}]}}\n"
                    "- Numbered: {type: 'numbered_list_item', numbered_list_item: {rich_text: [{type: 'text', text: {content: 'Step'}}]}}\n"
                    "- Toggle: {type: 'toggle', toggle: {rich_text: [{type: 'text', text: {content: 'Click to expand'}}]}}\n"
                    "- Callout: {type: 'callout', callout: {rich_text: [{type: 'text', text: {content: 'Note'}}], icon: {emoji: '💡'}}}\n"
                    "- Code: {type: 'code', code: {rich_text: [{type: 'text', text: {content: 'print()'}}], language: 'python'}}\n"
                    "- Quote: {type: 'quote', quote: {rich_text: [{type: 'text', text: {content: 'Quote text'}}]}}\n"
                    "- Divider: {type: 'divider', divider: {}}\n"
                    "- Paragraph: {type: 'paragraph', paragraph: {rich_text: [{type: 'text', text: {content: 'Text'}}]}}"
                ),
                "items": {"type": "object"},
            },
        },
        "required": ["parent_id", "blocks"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        parent_id = kwargs["parent_id"]
        blocks = kwargs.get("blocks", [])
        if not blocks:
            return "Error: No blocks provided."

        try:
            result = await self._client.patch(
                f"/blocks/{parent_id}/children", {"children": blocks}
            )
            count = len(result.get("results", []))
            return f"Appended {count} blocks."
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class NotionSearchTool(Tool):
    """Search Notion pages and databases."""

    name = "notion_search"
    description = "Search Notion for pages or databases by query text."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "filter_type": {
                "type": "string",
                "enum": ["page", "database"],
                "description": "Filter by object type",
            },
            "page_size": {
                "type": "integer",
                "description": "Max results (default 10)",
                "maximum": 100,
            },
        },
        "required": ["query"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        body: dict[str, Any] = {
            "query": kwargs["query"],
            "page_size": kwargs.get("page_size", 10),
        }
        if filter_type := kwargs.get("filter_type"):
            body["filter"] = {"value": filter_type, "property": "object"}

        try:
            data = await self._client.post("/search", body)
            results = []
            for item in data.get("results", []):
                if item.get("object") == "database":
                    results.append(_format_db(item))
                else:
                    results.append(_format_page(item))
            return json.dumps({"results": results}, default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------

class NotionCreateDatabaseTool(Tool):
    """Create a Notion database (kanban board, table, tracker, etc.)."""

    name = "notion_create_database"
    description = (
        "Create a new Notion database under a parent page. Use this for kanban boards, "
        "tables, project trackers, and any structured data. For kanban boards, include a "
        "status property. Define columns via the properties parameter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Database title"},
            "parent_page_id": {
                "type": "string",
                "description": "Parent page ID (uses default root if omitted)",
            },
            "properties": {
                "type": "object",
                "description": (
                    "Database column definitions as {name: definition}. Supported types:\n"
                    "- title: Primary name column (one required) — use {title: {}}\n"
                    "- rich_text: {rich_text: {}}\n"
                    "- number: {number: {format: 'number'}} (formats: number, percent, dollar, euro, etc.)\n"
                    "- select: {select: {options: [{name: 'Opt1', color: 'blue'}]}}\n"
                    "- multi_select: {multi_select: {options: [{name: 'Tag1', color: 'green'}]}}\n"
                    "- status: {status: {}} — Notion auto-creates To Do / In Progress / Done groups\n"
                    "- date: {date: {}}\n"
                    "- checkbox: {checkbox: {}}\n"
                    "- url: {url: {}}\n"
                    "- email: {email: {}}\n"
                    "- phone_number: {phone_number: {}}\n"
                    "- people: {people: {}}\n"
                    "- files: {files: {}}\n"
                    "- relation: {relation: {database_id: 'id', type: 'single_property'}}\n"
                    "- formula: {formula: {expression: 'prop(\"Price\") * 2'}}"
                ),
            },
            "is_inline": {
                "type": "boolean",
                "description": "If true, database appears inline within the parent page (default false)",
            },
        },
        "required": ["title"],
    }

    def __init__(self, client: _NotionClient, root_page_id: str = ""):
        self._client = client
        self._root_page_id = root_page_id

    async def execute(self, **kwargs: Any) -> str:
        parent_id = kwargs.get("parent_page_id") or self._root_page_id
        if not parent_id:
            return "Error: No parent page ID specified and no default root_page_id configured."

        raw_props = kwargs.get("properties", {})
        properties: dict[str, Any] = {}
        has_title = False

        for name, definition in raw_props.items():
            if isinstance(definition, dict):
                properties[name] = definition
                if "title" in definition:
                    has_title = True
            elif definition == "title":
                properties[name] = {"title": {}}
                has_title = True

        if not has_title:
            properties["Name"] = {"title": {}}

        body: dict[str, Any] = {
            "parent": {"page_id": parent_id},
            "title": _rich_text(kwargs["title"]),
            "properties": properties,
            "is_inline": kwargs.get("is_inline", False),
        }

        try:
            result = await self._client.post("/databases", body)
            return json.dumps(_format_db(result), default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionUpdateDatabaseTool(Tool):
    """Update a Notion database schema (add/rename columns, change title)."""

    name = "notion_update_database"
    description = (
        "Update a Notion database: change title, add new columns, or rename existing ones. "
        "Use this to modify database structure after creation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "database_id": {"type": "string", "description": "Database ID to update"},
            "title": {"type": "string", "description": "New database title"},
            "properties": {
                "type": "object",
                "description": (
                    "Property updates. To add a column: {name: {type_def}}. "
                    "To rename: {old_name: {name: 'new_name'}}. "
                    "To delete: {name: null}. Same format as create_database properties."
                ),
            },
        },
        "required": ["database_id"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        db_id = kwargs["database_id"]
        patch_body: dict[str, Any] = {}

        if title := kwargs.get("title"):
            patch_body["title"] = _rich_text(title)
        if props := kwargs.get("properties"):
            patch_body["properties"] = props

        if not patch_body:
            return "No changes specified."

        try:
            result = await self._client.patch(f"/databases/{db_id}", patch_body)
            return json.dumps(_format_db(result), default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionCreateDatabaseEntryTool(Tool):
    """Add a row/card to a Notion database."""

    name = "notion_create_database_entry"
    description = (
        "Add a new entry (row or card) to a Notion database. "
        "For kanban boards, set the status property to place the card in the right column."
    )
    parameters = {
        "type": "object",
        "properties": {
            "database_id": {"type": "string", "description": "Notion database ID"},
            "properties": {
                "type": "object",
                "description": (
                    "Property values as {name: value}. Use Notion API format:\n"
                    "- title (Name): {title: [{type: 'text', text: {content: 'Card title'}}]}\n"
                    "- status: {status: {name: 'In Progress'}}\n"
                    "- select: {select: {name: 'High'}}\n"
                    "- multi_select: {multi_select: [{name: 'Tag1'}]}\n"
                    "- date: {date: {start: '2025-03-01'}}\n"
                    "- checkbox: {checkbox: true}\n"
                    "- number: {number: 42}\n"
                    "- url: {url: 'https://...'}\n"
                    "- rich_text: {rich_text: [{type: 'text', text: {content: '...'}}]}\n"
                    "- people: {people: [{id: 'user-id'}]}\n"
                    "- Simple string values are auto-converted to rich_text."
                ),
            },
            "children": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Optional block content inside the entry (same format as append_blocks)",
            },
        },
        "required": ["database_id", "properties"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        db_id = kwargs["database_id"]
        raw_props = kwargs.get("properties", {})

        properties: dict[str, Any] = {}
        for key, val in raw_props.items():
            if isinstance(val, str):
                properties[key] = {
                    "rich_text": _rich_text(val)
                }
            else:
                properties[key] = val

        body: dict[str, Any] = {
            "parent": {"database_id": db_id},
            "properties": properties,
        }
        if children := kwargs.get("children"):
            body["children"] = children

        try:
            result = await self._client.post("/pages", body)
            return json.dumps(_format_page(result))
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionQueryDatabaseTool(Tool):
    """Query a Notion database with filters and sorts."""

    name = "notion_query_database"
    description = (
        "Query a Notion database with optional filters and sorts. Returns matching entries "
        "with all their property values."
    )
    parameters = {
        "type": "object",
        "properties": {
            "database_id": {"type": "string", "description": "Notion database ID"},
            "filter": {
                "type": "object",
                "description": (
                    "Notion filter object. Examples:\n"
                    "- Status equals: {property: 'Status', status: {equals: 'In Progress'}}\n"
                    "- Select equals: {property: 'Priority', select: {equals: 'High'}}\n"
                    "- Checkbox: {property: 'Done', checkbox: {equals: true}}\n"
                    "- Compound: {and: [{...}, {...}]} or {or: [{...}, {...}]}"
                ),
            },
            "sorts": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Sort criteria: [{property: 'Name', direction: 'ascending'}]",
            },
            "page_size": {
                "type": "integer",
                "description": "Max results (default 20)",
                "maximum": 100,
            },
        },
        "required": ["database_id"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        db_id = kwargs["database_id"]
        body: dict[str, Any] = {
            "page_size": kwargs.get("page_size", 20),
        }
        if filt := kwargs.get("filter"):
            body["filter"] = filt
        if sorts := kwargs.get("sorts"):
            body["sorts"] = sorts

        try:
            data = await self._client.post(f"/databases/{db_id}/query", body)
            results = [_format_page(item) for item in data.get("results", [])]
            return json.dumps({
                "total": len(results),
                "has_more": data.get("has_more", False),
                "results": results,
            }, default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"
