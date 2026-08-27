"""Sheets tools. The Sheets API's `values.get`/`values.update`/`values.append`
already return and accept structured cell grids directly (`{"values": [[...]]}`)
-- no text-extraction step is needed here, unlike Docs (see tools/docs.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import google_api


async def sheets_read_range(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    return await google_api.get_json(
        google_api.API_SHEETS,
        f"/spreadsheets/{args['spreadsheetId']}/values/{args['range']}",
        token=token,
    )


async def sheets_write_range(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    async with google_api.get_client() as client:
        resp = await client.put(
            f"{google_api.API_SHEETS}/spreadsheets/{args['spreadsheetId']}/values/{args['range']}",
            params={"valueInputOption": "USER_ENTERED"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"values": args["values"]},
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Google API error (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    result: dict[str, Any] = resp.json()
    return result


async def sheets_append_row(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    async with google_api.get_client() as client:
        resp = await client.post(
            f"{google_api.API_SHEETS}/spreadsheets/{args['spreadsheetId']}/values/{args['range']}:append",
            params={"valueInputOption": "USER_ENTERED"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"values": [args["row"]]},
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Google API error (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    result: dict[str, Any] = resp.json()
    return result


async def sheets_list_worksheets(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    spreadsheet = await google_api.get_json(
        google_api.API_SHEETS,
        f"/spreadsheets/{args['spreadsheetId']}",
        token=token,
        params={"fields": "sheets.properties"},
    )
    return [s["properties"]["title"] for s in spreadsheet.get("sheets", [])]


TOOLS: list[Tool] = [
    Tool(
        name="sheets_read_range",
        description="Read a cell range (A1 notation, e.g. Sheet1!A1:C10).",
        inputSchema={
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["spreadsheetId", "range"],
        },
    ),
    Tool(
        name="sheets_write_range",
        description="Write a grid of values into a range.",
        inputSchema={
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "range": {"type": "string"},
                "values": {"type": "array", "items": {"type": "array"}},
            },
            "required": ["spreadsheetId", "range", "values"],
        },
    ),
    Tool(
        name="sheets_append_row",
        description="Append one row after the last row with data in a range.",
        inputSchema={
            "type": "object",
            "properties": {
                "spreadsheetId": {"type": "string"},
                "range": {"type": "string"},
                "row": {"type": "array"},
            },
            "required": ["spreadsheetId", "range", "row"],
        },
    ),
    Tool(
        name="sheets_list_worksheets",
        description="List worksheet (tab) names in a spreadsheet.",
        inputSchema={
            "type": "object",
            "properties": {"spreadsheetId": {"type": "string"}},
            "required": ["spreadsheetId"],
        },
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "sheets_read_range": sheets_read_range,
    "sheets_write_range": sheets_write_range,
    "sheets_append_row": sheets_append_row,
    "sheets_list_worksheets": sheets_list_worksheets,
}
