"""Notion tools: create, get, update, search pages, and query/create database entries."""

from __future__ import annotations

import json
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool

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


def _text_to_blocks(text: str) -> list[dict]:
    """Convert plain text to Notion block objects (one paragraph per line group)."""
    blocks = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": line}}]
            },
        })
    return blocks


def _extract_title(page: dict) -> str:
    """Extract the plain-text title from a Notion page."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts)
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


class NotionCreatePageTool(Tool):
    """Create a Notion page."""

    name = "notion_create_page"
    description = "Create a new Notion page under a parent page."
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Page title"},
            "content": {"type": "string", "description": "Page body content (plain text)"},
            "parent_page_id": {"type": "string", "description": "Parent page ID (uses default root if omitted)"},
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

        body: dict[str, Any] = {
            "parent": {"page_id": parent_id},
            "properties": {
                "title": {
                    "title": [{"type": "text", "text": {"content": kwargs["title"]}}]
                }
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
    """Read a Notion page's properties and content."""

    name = "notion_get_page"
    description = "Get a Notion page's properties and block content."
    parameters = {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "Notion page ID"},
        },
        "required": ["page_id"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        page_id = kwargs["page_id"]
        try:
            page = await self._client.get(f"/pages/{page_id}")
            blocks = await self._client.get(f"/blocks/{page_id}/children")
            result = _format_page(page)
            result["blocks"] = blocks.get("results", [])
            return json.dumps(result, default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionUpdatePageTool(Tool):
    """Update a Notion page (title, content, or archive)."""

    name = "notion_update_page"
    description = "Update a Notion page: change title, append content, or archive it."
    parameters = {
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "Notion page ID"},
            "title": {"type": "string", "description": "New page title"},
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
            # Update properties (title, archived)
            patch_body: dict[str, Any] = {}
            if title := kwargs.get("title"):
                patch_body["properties"] = {
                    "title": {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                }
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
            "page_size": {"type": "integer", "description": "Max results (default 10)", "maximum": 100},
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
                if item.get("object") == "page":
                    results.append(_format_page(item))
                else:
                    results.append({
                        "id": item.get("id"),
                        "object": item.get("object"),
                        "title": _extract_title(item),
                        "url": item.get("url"),
                    })
            return json.dumps({"results": results}, default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class NotionCreateDatabaseEntryTool(Tool):
    """Add a row to a Notion database."""

    name = "notion_create_database_entry"
    description = "Add a new entry (row) to a Notion database."
    parameters = {
        "type": "object",
        "properties": {
            "database_id": {"type": "string", "description": "Notion database ID"},
            "properties": {
                "type": "object",
                "description": (
                    "Property values as {name: value}. For title properties use a string. "
                    "For select use {select: {name: 'Option'}}. For date use {date: {start: 'YYYY-MM-DD'}}. "
                    "For rich_text use a string. Other types use the Notion API property format."
                ),
            },
        },
        "required": ["database_id", "properties"],
    }

    def __init__(self, client: _NotionClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        db_id = kwargs["database_id"]
        raw_props = kwargs.get("properties", {})

        # Normalize simple string values into Notion property format
        properties: dict[str, Any] = {}
        for key, val in raw_props.items():
            if isinstance(val, str):
                # Guess: title if it's the first or named "Name"/"Title", else rich_text
                properties[key] = {
                    "rich_text": [{"type": "text", "text": {"content": val}}]
                }
            elif isinstance(val, dict):
                properties[key] = val
            else:
                properties[key] = val

        body = {
            "parent": {"database_id": db_id},
            "properties": properties,
        }

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
    description = "Query a Notion database with optional filters and sorts. Returns matching entries."
    parameters = {
        "type": "object",
        "properties": {
            "database_id": {"type": "string", "description": "Notion database ID"},
            "filter": {
                "type": "object",
                "description": "Notion filter object (see Notion API docs for format)",
            },
            "sorts": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Sort criteria array [{property: 'Name', direction: 'ascending'}]",
            },
            "page_size": {"type": "integer", "description": "Max results (default 20)", "maximum": 100},
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
