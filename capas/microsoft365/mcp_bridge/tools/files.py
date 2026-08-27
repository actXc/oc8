from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import graph


async def files_list(args: dict[str, Any]) -> Any:
    path = args.get("path", "")
    endpoint = (
        f"/drives/{args['driveId']}/root/children"
        if not path
        else f"/drives/{args['driveId']}/root:/{path}:/children"
    )
    result = await graph.get(endpoint)
    return result.get("value", [])


async def files_get_content(args: dict[str, Any]) -> Any:
    """A file's own content, decoded as text — format-agnostic on purpose.

    Deliberately NOT an extractor: this is the generic "read this file" tool
    for text/Markdown/CSV/JSON/HTML (the same set the connector's
    `DOWNLOAD_AS_TEXT` ingests byte-for-byte). The OOXML formats have their own
    parsing tools (`word_get_text`, `powerpoint_get_text`, `excel_read_range`)
    because each needs a different parser; teaching this one to sniff types
    would duplicate all three. The size cap lives in `graph.get_text`.
    """
    # get_text, not get: /content answers with the file itself (after a 302 to
    # a pre-authenticated download host), never with JSON.
    return await graph.get_text(f"/drives/{args['driveId']}/items/{args['itemId']}/content")


async def files_upload(args: dict[str, Any]) -> Any:
    content = args["content"]
    raw = content.encode() if isinstance(content, str) else content
    return await graph.put_bytes(
        f"/drives/{args['driveId']}/root:/{args['path']}:/content", raw
    )


async def files_update_content(args: dict[str, Any]) -> Any:
    content = args["content"]
    raw = content.encode() if isinstance(content, str) else content
    return await graph.put_bytes(
        f"/drives/{args['driveId']}/items/{args['itemId']}/content", raw
    )


async def files_delete(args: dict[str, Any]) -> Any:
    await graph.delete(f"/drives/{args['driveId']}/items/{args['itemId']}")
    return {"status": "deleted"}


async def files_search(args: dict[str, Any]) -> Any:
    result = await graph.get(
        f"/drives/{args['driveId']}/root/search(q='{args['query']}')"
    )
    return result.get("value", [])


async def sites_list(args: dict[str, Any]) -> Any:
    result = await graph.get("/sites", params={"search": args.get("query", "*")})
    return result.get("value", [])


TOOLS: list[Tool] = [
    Tool(
        name="files_list",
        description="List files in a drive, optionally under a path.",
        inputSchema={
            "type": "object",
            "properties": {"driveId": {"type": "string"}, "path": {"type": "string"}},
            "required": ["driveId"],
        },
    ),
    Tool(
        name="files_get_content",
        description=(
            "Get a text file's content (plain text, Markdown, CSV, JSON, HTML). "
            "For Word/PowerPoint/Excel use word_get_text, powerpoint_get_text or "
            "excel_read_range instead — this returns their raw bytes as text."
        ),
        inputSchema={
            "type": "object",
            "properties": {"driveId": {"type": "string"}, "itemId": {"type": "string"}},
            "required": ["driveId", "itemId"],
        },
    ),
    Tool(
        name="files_upload",
        description="Upload a new file at the given path.",
        inputSchema={
            "type": "object",
            "properties": {
                "driveId": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["driveId", "path", "content"],
        },
    ),
    Tool(
        name="files_update_content",
        description="Replace an existing file's content.",
        inputSchema={
            "type": "object",
            "properties": {
                "driveId": {"type": "string"},
                "itemId": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["driveId", "itemId", "content"],
        },
    ),
    Tool(
        name="files_delete",
        description="Delete a file.",
        inputSchema={
            "type": "object",
            "properties": {"driveId": {"type": "string"}, "itemId": {"type": "string"}},
            "required": ["driveId", "itemId"],
        },
    ),
    Tool(
        name="files_search",
        description="Search for files in a drive by name/content.",
        inputSchema={
            "type": "object",
            "properties": {"driveId": {"type": "string"}, "query": {"type": "string"}},
            "required": ["driveId", "query"],
        },
    ),
    Tool(
        name="sites_list",
        description="List SharePoint sites reachable by this app registration.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "files_list": files_list,
    "files_get_content": files_get_content,
    "files_upload": files_upload,
    "files_update_content": files_update_content,
    "files_delete": files_delete,
    "files_search": files_search,
    "sites_list": sites_list,
}
