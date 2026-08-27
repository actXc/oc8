"""Slides tools. `slides_replace_placeholder_text` targets ONE shape by object
ID (delete its existing text, then insert the replacement) -- genuinely more
granular than a whole-file replace. This is the honest opposite of the
Microsoft plugin's PowerPoint tools, which are whole-file-only because Graph
has no slide-level editing API at all (see that plugin's tools_powerpoint.py
docstring, and design doc §4.2's own note about this asymmetry)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import google_api


def _extract_text_content(text_content: dict[str, Any], parts: list[str]) -> None:
    for text_element in text_content.get("textElements", []):
        text_run = text_element.get("textRun")
        if text_run is not None:
            parts.append(text_run.get("content", ""))


def _extract_page_elements(page_elements: list[Any], parts: list[str]) -> None:
    """Walk one `PageElement[]`, appending every TextRun's content.

    A PageElement is a union, and only ONE of its arms used to be read. The
    other two carry text just as real: a `table` nests it at
    `table.tableRows[].tableCells[].text`, and an `elementGroup` (what the
    Slides editor produces the moment someone selects two shapes and groups
    them) nests further PageElements at `elementGroup.children`, arbitrarily
    deep. Skipping them made `slides_get_text` return a subset while promising
    "all text from every slide", with nothing to tell the caller that content
    was dropped."""
    for element in page_elements:
        shape = element.get("shape")
        if shape is not None:
            _extract_text_content(shape.get("text", {}), parts)
            continue
        table = element.get("table")
        if table is not None:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    _extract_text_content(cell.get("text", {}), parts)
            continue
        group = element.get("elementGroup")
        if group is not None:
            _extract_page_elements(group.get("children", []), parts)


def _extract_presentation_text(presentation: dict[str, Any]) -> str:
    parts: list[str] = []
    for slide in presentation.get("slides", []):
        _extract_page_elements(slide.get("pageElements", []), parts)
    return "".join(parts)


async def slides_get_text(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    presentation = await google_api.get_json(
        google_api.API_SLIDES, f"/presentations/{args['presentationId']}", token=token
    )
    return _extract_presentation_text(presentation)


async def slides_replace_placeholder_text(args: dict[str, Any]) -> Any:
    """The Slides API 400s on `deleteText` against a shape with NO existing
    text (an empty placeholder). Fetch the presentation first and only issue
    `deleteText` if the target shape actually has text to delete -- `insertText`
    is safe unconditionally."""
    token = google_api.self_token()
    presentation = await google_api.get_json(
        google_api.API_SLIDES, f"/presentations/{args['presentationId']}", token=token
    )
    has_existing_text = any(
        element.get("objectId") == args["objectId"]
        and element.get("shape", {}).get("text", {}).get("textElements")
        for slide in presentation.get("slides", [])
        for element in slide.get("pageElements", [])
    )
    requests: list[dict[str, Any]] = []
    if has_existing_text:
        requests.append(
            {"deleteText": {"objectId": args["objectId"], "textRange": {"type": "ALL"}}}
        )
    requests.append(
        {
            "insertText": {
                "objectId": args["objectId"],
                "text": args["text"],
                "insertionIndex": 0,
            }
        }
    )
    return await google_api.post_json(
        google_api.API_SLIDES,
        f"/presentations/{args['presentationId']}:batchUpdate",
        token=token,
        json={"requests": requests},
    )


async def slides_create_from_template(args: dict[str, Any]) -> Any:
    """Copies `templatePresentationId` (a Drive file, like Docs) into `driveId`,
    then applies each `{objectId, text}` replacement via
    slides_replace_placeholder_text."""
    token = google_api.self_token()
    copied = await google_api.post_json(
        google_api.API_DRIVE,
        f"/files/{args['templatePresentationId']}/copy",
        token=token,
        json={"name": args["name"], "parents": [args["driveId"]]},
        params={"supportsAllDrives": "true"},
    )
    new_id = copied["id"]
    for replacement in args.get("replacements", []):
        await slides_replace_placeholder_text(
            {
                "presentationId": new_id,
                "objectId": replacement["objectId"],
                "text": replacement["text"],
            }
        )
    return {"presentationId": new_id}


TOOLS: list[Tool] = [
    Tool(
        name="slides_get_text",
        description="Extract all text from every slide's shapes.",
        inputSchema={
            "type": "object",
            "properties": {"presentationId": {"type": "string"}},
            "required": ["presentationId"],
        },
    ),
    Tool(
        name="slides_replace_placeholder_text",
        description=(
            "Replace one shape's text by its object ID (targeted, not whole-file). "
            "Safe to call whether or not the shape currently has text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "presentationId": {"type": "string"},
                "objectId": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["presentationId", "objectId", "text"],
        },
    ),
    Tool(
        name="slides_create_from_template",
        description="Copy a template presentation into a Shared Drive, then apply targeted placeholder replacements.",
        inputSchema={
            "type": "object",
            "properties": {
                "templatePresentationId": {"type": "string"},
                "driveId": {"type": "string"},
                "name": {"type": "string"},
                "replacements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "objectId": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["templatePresentationId", "driveId", "name"],
        },
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "slides_get_text": slides_get_text,
    "slides_replace_placeholder_text": slides_replace_placeholder_text,
    "slides_create_from_template": slides_create_from_template,
}
