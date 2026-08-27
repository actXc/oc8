"""Outlook Mail tools (design doc §4.2). Each handler returns plain JSON-
serializable data -- the MCP server layer (`__main__.py`, Task 13) wraps it
into the SDK's own `types.TextContent`, not this module's job.

Every path here is `/users/{id}/...`, never `/me/...`: this connection's token
is app-only and Graph rejects `/me` outright under it. `_user.resolve_user`
says where the id comes from and why.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import graph
from .._user import USER_ID_PROPERTY, resolve_user


async def mail_search(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    result = await graph.get(f"/users/{user}/messages", params={"$search": f'"{args["query"]}"'})
    return result.get("value", [])


async def mail_get(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    return await graph.get(f"/users/{user}/messages/{args['messageId']}")


async def mail_send(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    await graph.post(
        f"/users/{user}/sendMail",
        {
            "message": {
                "subject": args["subject"],
                "body": {"contentType": "Text", "content": args["body"]},
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in args["to"]],
            }
        },
    )
    return {"status": "sent"}


async def mail_reply(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    await graph.post(f"/users/{user}/messages/{args['messageId']}/reply", {"comment": args["body"]})
    return {"status": "sent"}


async def mail_create_draft(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    return await graph.post(
        f"/users/{user}/messages",
        {
            "subject": args["subject"],
            "body": {"contentType": "Text", "content": args["body"]},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in args.get("to", [])],
        },
    )


async def mail_move(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    return await graph.post(
        f"/users/{user}/messages/{args['messageId']}/move",
        {"destinationId": args["destinationFolderId"]},
    )


async def mail_delete(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    await graph.delete(f"/users/{user}/messages/{args['messageId']}")
    return {"status": "deleted"}


async def mail_list_folders(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    result = await graph.get(f"/users/{user}/mailFolders")
    return result.get("value", [])


async def mail_mark_read(args: dict[str, Any]) -> Any:
    user = resolve_user(args)
    return await graph.patch(
        f"/users/{user}/messages/{args['messageId']}", {"isRead": args.get("isRead", True)}
    )


#: Appended to every description below, so the addressing rule is stated on the
#: surface the model actually reads rather than only in this file's docstring.
_MAILBOX = (
    " Acts on the mailbox named by `userId`, or the connection's configured "
    "default mailbox when `userId` is omitted."
)


TOOLS: list[Tool] = [
    Tool(
        name="mail_search",
        description="Search a mailbox for messages matching a query." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}, **USER_ID_PROPERTY},
            "required": ["query"],
        },
    ),
    Tool(
        name="mail_get",
        description="Get one message by id." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {"messageId": {"type": "string"}, **USER_ID_PROPERTY},
            "required": ["messageId"],
        },
    ),
    Tool(
        name="mail_send",
        description="Send a new email." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {
                "to": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                **USER_ID_PROPERTY,
            },
            "required": ["to", "subject", "body"],
        },
    ),
    Tool(
        name="mail_reply",
        description="Reply to an existing message." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {
                "messageId": {"type": "string"},
                "body": {"type": "string"},
                **USER_ID_PROPERTY,
            },
            "required": ["messageId", "body"],
        },
    ),
    Tool(
        name="mail_create_draft",
        description="Create a draft email without sending it." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {
                "to": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                **USER_ID_PROPERTY,
            },
            "required": ["subject", "body"],
        },
    ),
    Tool(
        name="mail_move",
        description="Move a message to a different folder." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {
                "messageId": {"type": "string"},
                "destinationFolderId": {"type": "string"},
                **USER_ID_PROPERTY,
            },
            "required": ["messageId", "destinationFolderId"],
        },
    ),
    Tool(
        name="mail_delete",
        description="Delete a message." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {"messageId": {"type": "string"}, **USER_ID_PROPERTY},
            "required": ["messageId"],
        },
    ),
    Tool(
        name="mail_list_folders",
        description="List a mailbox's folders." + _MAILBOX,
        inputSchema={"type": "object", "properties": {**USER_ID_PROPERTY}},
    ),
    Tool(
        name="mail_mark_read",
        description="Mark a message read or unread." + _MAILBOX,
        inputSchema={
            "type": "object",
            "properties": {
                "messageId": {"type": "string"},
                "isRead": {"type": "boolean"},
                **USER_ID_PROPERTY,
            },
            "required": ["messageId"],
        },
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "mail_search": mail_search,
    "mail_get": mail_get,
    "mail_send": mail_send,
    "mail_reply": mail_reply,
    "mail_create_draft": mail_create_draft,
    "mail_move": mail_move,
    "mail_delete": mail_delete,
    "mail_list_folders": mail_list_folders,
    "mail_mark_read": mail_mark_read,
}
