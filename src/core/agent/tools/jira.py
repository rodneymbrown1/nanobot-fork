"""Jira tools: create, list, get, update, comment, and search issues."""

from __future__ import annotations

import json
from base64 import b64encode
from typing import Any

import httpx

from core.agent.tools.base import Tool


class _JiraClient:
    """Shared async HTTP helper for Jira REST API v3."""

    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self._auth = b64encode(f"{email}:{api_token}".encode()).decode()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Basic {self._auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}{path}",
                headers=self._headers(),
                params=params,
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json()

    async def post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=body,
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json() if r.content else {}

    async def put(self, path: str, body: dict) -> int:
        async with httpx.AsyncClient() as client:
            r = await client.put(
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=body,
                timeout=30.0,
            )
            r.raise_for_status()
            return r.status_code


def _text_to_adf(text: str) -> dict:
    """Convert plain text to Atlassian Document Format."""
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            }
            for line in text.split("\n")
            if line
        ],
    }


def _format_issue(issue: dict) -> dict:
    """Extract key fields from a Jira issue for LLM consumption."""
    fields = issue.get("fields", {})
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary"),
        "status": (fields.get("status") or {}).get("name"),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "priority": (fields.get("priority") or {}).get("name"),
        "issuetype": (fields.get("issuetype") or {}).get("name"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "description": fields.get("description"),
    }


class JiraCreateIssueTool(Tool):
    """Create a Jira issue."""

    name = "jira_create_issue"
    description = "Create a new Jira issue. Returns the issue key."
    parameters = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Issue title"},
            "description": {"type": "string", "description": "Issue description (plain text)"},
            "issue_type": {
                "type": "string",
                "description": "Issue type (Task, Bug, Story, Epic)",
                "default": "Task",
            },
            "project": {"type": "string", "description": "Project key (uses default if omitted)"},
            "assignee_id": {"type": "string", "description": "Atlassian account ID of assignee"},
            "priority": {"type": "string", "description": "Priority name (Highest, High, Medium, Low, Lowest)"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels to apply",
            },
        },
        "required": ["summary"],
    }

    def __init__(self, client: _JiraClient, default_project: str = ""):
        self._client = client
        self._default_project = default_project

    async def execute(self, **kwargs: Any) -> str:
        project = kwargs.get("project") or self._default_project
        if not project:
            return "Error: No project specified and no default project configured."

        fields: dict[str, Any] = {
            "project": {"key": project},
            "summary": kwargs["summary"],
            "issuetype": {"name": kwargs.get("issue_type", "Task")},
        }
        if desc := kwargs.get("description"):
            fields["description"] = _text_to_adf(desc)
        if assignee := kwargs.get("assignee_id"):
            fields["assignee"] = {"accountId": assignee}
        if priority := kwargs.get("priority"):
            fields["priority"] = {"name": priority}
        if labels := kwargs.get("labels"):
            fields["labels"] = labels

        try:
            result = await self._client.post("/rest/api/3/issue", {"fields": fields})
            return json.dumps({"key": result.get("key"), "self": result.get("self")})
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class JiraListIssuesTool(Tool):
    """List Jira issues with optional filters."""

    name = "jira_list_issues"
    description = "List Jira issues. Filter by project, status, or assignee."
    parameters = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project key (uses default if omitted)"},
            "status": {"type": "string", "description": "Filter by status (e.g. 'To Do', 'In Progress', 'Done')"},
            "assignee": {"type": "string", "description": "Filter by assignee ('currentUser()' or account ID)"},
            "max_results": {"type": "integer", "description": "Max results (default 20)", "maximum": 100},
        },
    }

    def __init__(self, client: _JiraClient, default_project: str = ""):
        self._client = client
        self._default_project = default_project

    async def execute(self, **kwargs: Any) -> str:
        project = kwargs.get("project") or self._default_project
        parts = []
        if project:
            parts.append(f'project = "{project}"')
        if status := kwargs.get("status"):
            parts.append(f'status = "{status}"')
        if assignee := kwargs.get("assignee"):
            parts.append(f"assignee = {assignee}")
        jql = " AND ".join(parts) if parts else "ORDER BY updated DESC"
        if parts:
            jql += " ORDER BY updated DESC"

        try:
            data = await self._client.get(
                "/rest/api/3/search/jql",
                params={"jql": jql, "maxResults": kwargs.get("max_results", 20)},
            )
            issues = [_format_issue(i) for i in data.get("issues", [])]
            return json.dumps({"total": data.get("total", 0), "issues": issues}, default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class JiraGetIssueTool(Tool):
    """Get full details of a Jira issue."""

    name = "jira_get_issue"
    description = "Get full details of a Jira issue by key (e.g. PROJ-123)."
    parameters = {
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "Issue key (e.g. PROJ-123)"},
        },
        "required": ["issue_key"],
    }

    def __init__(self, client: _JiraClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        key = kwargs["issue_key"]
        try:
            data = await self._client.get(f"/rest/api/3/issue/{key}")
            return json.dumps(_format_issue(data), default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class JiraUpdateIssueTool(Tool):
    """Update a Jira issue's fields or transition its status."""

    name = "jira_update_issue"
    description = "Update a Jira issue (change summary, description, assignee, priority, or transition status)."
    parameters = {
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "Issue key (e.g. PROJ-123)"},
            "summary": {"type": "string", "description": "New summary"},
            "description": {"type": "string", "description": "New description (plain text)"},
            "assignee_id": {"type": "string", "description": "New assignee account ID"},
            "priority": {"type": "string", "description": "New priority name"},
            "transition": {"type": "string", "description": "Transition name (e.g. 'In Progress', 'Done')"},
        },
        "required": ["issue_key"],
    }

    def __init__(self, client: _JiraClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        key = kwargs["issue_key"]
        results = []

        # Field updates
        fields: dict[str, Any] = {}
        if summary := kwargs.get("summary"):
            fields["summary"] = summary
        if desc := kwargs.get("description"):
            fields["description"] = _text_to_adf(desc)
        if assignee := kwargs.get("assignee_id"):
            fields["assignee"] = {"accountId": assignee}
        if priority := kwargs.get("priority"):
            fields["priority"] = {"name": priority}

        try:
            if fields:
                await self._client.put(f"/rest/api/3/issue/{key}", {"fields": fields})
                results.append("Fields updated.")

            if transition_name := kwargs.get("transition"):
                transitions = await self._client.get(f"/rest/api/3/issue/{key}/transitions")
                match = next(
                    (t for t in transitions.get("transitions", [])
                     if t["name"].lower() == transition_name.lower()),
                    None,
                )
                if match:
                    await self._client.post(
                        f"/rest/api/3/issue/{key}/transitions",
                        {"transition": {"id": match["id"]}},
                    )
                    results.append(f"Transitioned to '{match['name']}'.")
                else:
                    available = [t["name"] for t in transitions.get("transitions", [])]
                    results.append(f"Transition '{transition_name}' not found. Available: {available}")

            return " ".join(results) if results else "No changes specified."
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class JiraAddCommentTool(Tool):
    """Add a comment to a Jira issue."""

    name = "jira_add_comment"
    description = "Add a comment to a Jira issue."
    parameters = {
        "type": "object",
        "properties": {
            "issue_key": {"type": "string", "description": "Issue key (e.g. PROJ-123)"},
            "body": {"type": "string", "description": "Comment text (plain text)"},
        },
        "required": ["issue_key", "body"],
    }

    def __init__(self, client: _JiraClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        key = kwargs["issue_key"]
        body = _text_to_adf(kwargs["body"])
        try:
            result = await self._client.post(
                f"/rest/api/3/issue/{key}/comment", {"body": body}
            )
            return json.dumps({"id": result.get("id"), "created": result.get("created")})
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"


class JiraSearchTool(Tool):
    """Search Jira with JQL."""

    name = "jira_search"
    description = "Search Jira issues using JQL (Jira Query Language)."
    parameters = {
        "type": "object",
        "properties": {
            "jql": {"type": "string", "description": "JQL query string"},
            "max_results": {"type": "integer", "description": "Max results (default 20)", "maximum": 100},
        },
        "required": ["jql"],
    }

    def __init__(self, client: _JiraClient):
        self._client = client

    async def execute(self, **kwargs: Any) -> str:
        try:
            data = await self._client.get(
                "/rest/api/3/search/jql",
                params={"jql": kwargs["jql"], "maxResults": kwargs.get("max_results", 20)},
            )
            issues = [_format_issue(i) for i in data.get("issues", [])]
            return json.dumps({"total": data.get("total", 0), "issues": issues}, default=str)
        except httpx.HTTPStatusError as e:
            return f"Error {e.response.status_code}: {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {e}"
