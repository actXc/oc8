"""Docs tools. `_extract_document_text` walks the ACTUAL Docs API document
structure (Document.body.content -> StructuralElement -> Paragraph ->
ParagraphElement -> TextRun.content) rather than returning the raw nested
response -- that structure is real document metadata (sections, tables, lists),
not prose, and handing it back as-is would be the exact class of defect the
Microsoft 365 plugin's whole-branch review caught in its Word tool (returning
undecoded file bytes instead of extracted text, finding N2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import google_api


def _extract_structural_elements(content: list[Any], parts: list[str]) -> None:
    """Walk one `StructuralElement[]` list, appending every TextRun's content.

    Tables are NOT skipped. A Docs table nests real prose the same way the body
    does -- `table.tableRows[].tableCells[].content[]` is itself a
    `StructuralElement[]`, so this recurses into it -- and a template-derived
    document (exactly what `docs_create_from_template` produces) is very often
    mostly table. A tool that promises "all prose text" and silently returns a
    subset the caller has no way to detect is the same class of defect as the
    Microsoft 365 plugin's N2 finding, just quieter.

    `tableOfContents.content` is likewise a `StructuralElement[]`, and is
    likewise recursed into; `sectionBreak` carries no text at all."""
    for element in content:
        paragraph = element.get("paragraph")
        if paragraph is not None:
            for para_element in paragraph.get("elements", []):
                text_run = para_element.get("textRun")
                if text_run is not None:
                    parts.append(text_run.get("content", ""))
            continue
        table = element.get("table")
        if table is not None:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    _extract_structural_elements(cell.get("content", []), parts)
            continue
        toc = element.get("tableOfContents")
        if toc is not None:
            _extract_structural_elements(toc.get("content", []), parts)


def _extract_document_text(document: dict[str, Any]) -> str:
    parts: list[str] = []
    _extract_structural_elements(document.get("body", {}).get("content", []), parts)
    return "".join(parts)


async def docs_get_text(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    document = await google_api.get_json(
        google_api.API_DOCS, f"/documents/{args['documentId']}", token=token
    )
    return _extract_document_text(document)


async def docs_replace_text(args: dict[str, Any]) -> Any:
    token = google_api.self_token()
    return await google_api.post_json(
        google_api.API_DOCS,
        f"/documents/{args['documentId']}:batchUpdate",
        token=token,
        json={
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {"text": args["find"], "matchCase": True},
                        "replaceText": args["replace"],
                    }
                }
            ]
        },
    )


async def docs_create_from_template(args: dict[str, Any]) -> Any:
    """Copies `templateDocumentId` (Drive's own copy endpoint -- Docs files are
    Drive files) into `driveId`, then runs the same replaceAllText batchUpdate
    docs_replace_text uses, once per `{find, replace}` pair in `replacements`."""
    token = google_api.self_token()
    copied = await google_api.post_json(
        google_api.API_DRIVE,
        f"/files/{args['templateDocumentId']}/copy",
        token=token,
        json={"name": args["name"], "parents": [args["driveId"]]},
        params={"supportsAllDrives": "true"},
    )
    new_id = copied["id"]
    for pair in args.get("replacements", []):
        await docs_replace_text(
            {"documentId": new_id, "find": pair["find"], "replace": pair["replace"]}
        )
    return {"documentId": new_id}


TOOLS: list[Tool] = [
    Tool(
        name="docs_get_text",
        description="Extract all prose text from a Google Doc.",
        inputSchema={
            "type": "object",
            "properties": {"documentId": {"type": "string"}},
            "required": ["documentId"],
        },
    ),
    Tool(
        name="docs_replace_text",
        description="Find-and-replace all occurrences of a string in a Google Doc.",
        inputSchema={
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
            },
            "required": ["documentId", "find", "replace"],
        },
    ),
    Tool(
        name="docs_create_from_template",
        description=(
            "Copy a template Google Doc into a Shared Drive, then apply find/replace pairs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "templateDocumentId": {"type": "string"},
                "driveId": {"type": "string"},
                "name": {"type": "string"},
                "replacements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"find": {"type": "string"}, "replace": {"type": "string"}},
                    },
                },
            },
            "required": ["templateDocumentId", "driveId", "name"],
        },
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "docs_get_text": docs_get_text,
    "docs_replace_text": docs_replace_text,
    "docs_create_from_template": docs_create_from_template,
}
