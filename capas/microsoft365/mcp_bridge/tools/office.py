"""Word (whole-document text only — Graph has no paragraph-level Word editing
API, see the design doc §4.2) and Excel (genuinely granular via the Workbook
API) tools.

`/content` answers with the raw `.docx` bytes — Graph has no server-side
"export this document as text" (unlike Google Drive's export, design doc §3),
so `word_get_text` parses locally with python-docx, exactly as
`powerpoint.py` does for `.pptx` and the RAG connector's own
`_extract_docx_text` (Task 6) does for the knowledge base. Duplicated rather
than imported for the same reason stated there: the connector and this MCP
bridge are separate Python packages with no shared dependency.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import graph


def _extract_docx_text(raw: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(raw))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


async def word_get_text(args: dict[str, Any]) -> Any:
    # get_bytes, not get: /content answers with the file itself (after a 302 to
    # a pre-authenticated download host), never with JSON -- and a .docx is a
    # zip archive, so decoding it as text yields mojibake, not prose.
    # graph.get_bytes caps the download at the connector's own _MAX_BYTES.
    raw = await graph.get_bytes(f"/drives/{args['driveId']}/items/{args['itemId']}/content")
    return _extract_docx_text(raw)


async def word_replace_text(args: dict[str, Any]) -> Any:
    """Whole-document replace: re-upload new content at the same item id.
    There is no Graph endpoint for a targeted find/replace inside a .docx."""
    raw = args["newContent"].encode() if isinstance(args["newContent"], str) else args["newContent"]
    return await graph.put_bytes(f"/drives/{args['driveId']}/items/{args['itemId']}/content", raw)


async def excel_read_range(args: dict[str, Any]) -> Any:
    result = await graph.get(
        f"/drives/{args['driveId']}/items/{args['itemId']}/workbook/worksheets/{args['worksheet']}"
        f"/range(address='{args['range']}')"
    )
    return result.get("values", [])


async def excel_write_range(args: dict[str, Any]) -> Any:
    return await graph.patch(
        f"/drives/{args['driveId']}/items/{args['itemId']}/workbook/worksheets/{args['worksheet']}"
        f"/range(address='{args['range']}')",
        {"values": args["values"]},
    )


async def excel_append_row(args: dict[str, Any]) -> Any:
    return await graph.post(
        f"/drives/{args['driveId']}/items/{args['itemId']}/workbook/tables/{args['table']}/rows/add",
        {"values": [args["values"]]},
    )


async def excel_list_worksheets(args: dict[str, Any]) -> Any:
    result = await graph.get(f"/drives/{args['driveId']}/items/{args['itemId']}/workbook/worksheets")
    return result.get("value", [])


TOOLS: list[Tool] = [
    Tool(
        name="word_get_text",
        description="Extract all paragraph text from a Word document (no formatting or structure).",
        inputSchema={
            "type": "object",
            "properties": {"driveId": {"type": "string"}, "itemId": {"type": "string"}},
            "required": ["driveId", "itemId"],
        },
    ),
    Tool(
        name="word_replace_text",
        description="Replace a Word document's entire content (no targeted find/replace is available).",
        inputSchema={
            "type": "object",
            "properties": {
                "driveId": {"type": "string"},
                "itemId": {"type": "string"},
                "newContent": {"type": "string"},
            },
            "required": ["driveId", "itemId", "newContent"],
        },
    ),
    Tool(
        name="excel_read_range",
        description="Read a cell range from a worksheet.",
        inputSchema={
            "type": "object",
            "properties": {
                "driveId": {"type": "string"},
                "itemId": {"type": "string"},
                "worksheet": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["driveId", "itemId", "worksheet", "range"],
        },
    ),
    Tool(
        name="excel_write_range",
        description="Write values into a cell range.",
        inputSchema={
            "type": "object",
            "properties": {
                "driveId": {"type": "string"},
                "itemId": {"type": "string"},
                "worksheet": {"type": "string"},
                "range": {"type": "string"},
                "values": {"type": "array"},
            },
            "required": ["driveId", "itemId", "worksheet", "range", "values"],
        },
    ),
    Tool(
        name="excel_append_row",
        description="Append a row to a named table.",
        inputSchema={
            "type": "object",
            "properties": {
                "driveId": {"type": "string"},
                "itemId": {"type": "string"},
                "table": {"type": "string"},
                "values": {"type": "array"},
            },
            "required": ["driveId", "itemId", "table", "values"],
        },
    ),
    Tool(
        name="excel_list_worksheets",
        description="List a workbook's worksheets.",
        inputSchema={
            "type": "object",
            "properties": {"driveId": {"type": "string"}, "itemId": {"type": "string"}},
            "required": ["driveId", "itemId"],
        },
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "word_get_text": word_get_text,
    "word_replace_text": word_replace_text,
    "excel_read_range": excel_read_range,
    "excel_write_range": excel_write_range,
    "excel_append_row": excel_append_row,
    "excel_list_worksheets": excel_list_worksheets,
}
