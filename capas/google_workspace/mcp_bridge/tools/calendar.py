"""Calendar tools. `calendar_create_meet_link` IS the Meet integration -- there
is no separate Meet API to speak of; setting `conferenceData.createRequest` on
a create/update call is what Google Calendar's own API uses to attach a Meet
link (design doc §4.2)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import Tool

from .. import google_api

_EVENT_FIELDS = {
    "summary": {"type": "string"},
    "start": {"type": "string", "description": "RFC3339 datetime"},
    "end": {"type": "string", "description": "RFC3339 datetime"},
    "attendees": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Attendee email addresses",
    },
    "mailbox": {
        "type": "string",
        "description": "Calendar owner; omit to use this connection's default.",
    },
}


def _event_body(args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": args.get("summary", ""),
        "start": {"dateTime": args["start"]},
        "end": {"dateTime": args["end"]},
    }
    if args.get("attendees"):
        body["attendees"] = [{"email": a} for a in args["attendees"]]
    return body


async def calendar_list_events(args: dict[str, Any]) -> Any:
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    return await google_api.get_json(
        google_api.API_CALENDAR,
        f"/calendars/{mailbox}/events",
        token=token,
        params={"timeMin": args.get("timeMin", ""), "timeMax": args.get("timeMax", "")},
    )


async def calendar_get_event(args: dict[str, Any]) -> Any:
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    return await google_api.get_json(
        google_api.API_CALENDAR, f"/calendars/{mailbox}/events/{args['eventId']}", token=token
    )


async def calendar_create_event(args: dict[str, Any]) -> Any:
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    return await google_api.post_json(
        google_api.API_CALENDAR,
        f"/calendars/{mailbox}/events",
        token=token,
        json=_event_body(args),
    )


async def _merged_attendees(
    mailbox: str, event_id: str, token: str, requested: list[Any]
) -> list[dict[str, Any]]:
    """The event's CURRENT attendees plus the requested ones.

    Read-modify-write for the same reason `calendar_respond_to_invite` does it:
    `attendees` is replaced WHOLESALE by events.patch, not merged. PATCHing the
    one address a caller asked to add would silently uninvite everybody already
    on the meeting -- HTTP 200, no error, and the people removed only find out
    when the event vanishes from their calendar.

    Existing entries are carried over UNTOUCHED (their `responseStatus`,
    `organizer`, `optional` flags and so on are Google's, not ours to re-state),
    and an address already on the event is not added a second time -- matched
    case-insensitively, since email local-parts are case-insensitive in practice
    and Google treats them so.
    """
    event = await google_api.get_json(
        google_api.API_CALENDAR, f"/calendars/{mailbox}/events/{event_id}", token=token
    )
    existing = [a for a in (event.get("attendees") or []) if isinstance(a, dict)]
    merged = list(existing)
    known = {str(a.get("email", "")).casefold() for a in existing}
    for email in requested:
        if str(email).casefold() not in known:
            merged.append({"email": email})
            known.add(str(email).casefold())
    return merged


async def calendar_update_event(args: dict[str, Any]) -> Any:
    """`attendees` ADDS people; it never removes anyone.

    Only an update that actually names `attendees` pays for the extra GET --
    a reschedule or a retitle stays the single PATCH it always was, since
    nothing about it can drop an attendee.
    """
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    body = _event_body(args)
    if args.get("attendees"):
        body["attendees"] = await _merged_attendees(
            mailbox, args["eventId"], token, list(args["attendees"])
        )
    return await google_api.patch_json(
        google_api.API_CALENDAR,
        f"/calendars/{mailbox}/events/{args['eventId']}",
        token=token,
        json=body,
    )


async def calendar_cancel_event(args: dict[str, Any]) -> Any:
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    await google_api.delete(
        google_api.API_CALENDAR, f"/calendars/{mailbox}/events/{args['eventId']}", token=token
    )
    return {"cancelled": args["eventId"]}


async def calendar_respond_to_invite(args: dict[str, Any]) -> Any:
    """Read-modify-write, not a blind PATCH: `attendees` is replaced wholesale
    by events.patch (not merged), and `self` is output-only on EventAttendee
    (it cannot be used to select which attendee to modify). So this GETs the
    event, finds the attendee whose `email` matches the acting mailbox, flips
    only that entry's `responseStatus`, and PATCHes back the complete list --
    otherwise every other attendee is silently removed from the meeting."""
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    target = google_api.resolve_mailbox_address(args.get("mailbox"))
    event = await google_api.get_json(
        google_api.API_CALENDAR, f"/calendars/{mailbox}/events/{args['eventId']}", token=token
    )
    attendees = event.get("attendees", [])
    updated = []
    found = False
    for attendee in attendees:
        if attendee.get("email", "").casefold() == target.casefold():
            attendee = {**attendee, "responseStatus": args["response"]}
            found = True
        updated.append(attendee)
    if not found:
        raise RuntimeError(f"mailbox {target!r} is not an attendee on event {args['eventId']!r}")
    return await google_api.patch_json(
        google_api.API_CALENDAR,
        f"/calendars/{mailbox}/events/{args['eventId']}",
        token=token,
        json={"attendees": updated},
    )


async def calendar_check_free_busy(args: dict[str, Any]) -> Any:
    """`FreeBusyRequestItem.id` is a CALENDAR ID -- a real address -- not the
    `primary` alias every other call in this module puts in its URL path.
    `primary` only resolves as a path segment (`/calendars/primary/...`); sent
    as an item id it is just an unknown calendar, and `freeBusy` answers an
    unknown calendar with HTTP **200** carrying a per-calendar `errors` block,
    so `_unwrap` raises nothing and the model reads a failure as an answer. So
    the item id is always `resolve_mailbox_address`'s real return value.

    Free/busy also exists to find a slot across OTHER people's calendars, which
    is why `calendars` is accepted alongside the acting mailbox: `items` takes
    a list, and the colleagues being scheduled around are usually not the
    mailbox this call authenticates as. Those extra calendars are NOT checked
    against the delegated-mailbox allow-list -- reading free/busy is what
    Calendar exposes to anyone in the domain, and requiring delegation for each
    of them would make the multi-person case impossible to express."""
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    acting = google_api.resolve_mailbox_address(args.get("mailbox"))
    ids = [acting, *(args.get("calendars") or [])]
    seen: dict[str, None] = {}
    for calendar_id in ids:
        seen.setdefault(str(calendar_id), None)
    return await google_api.post_json(
        google_api.API_CALENDAR,
        "/freeBusy",
        token=token,
        json={
            "timeMin": args["timeMin"],
            "timeMax": args["timeMax"],
            "items": [{"id": calendar_id} for calendar_id in seen],
        },
    )


async def calendar_create_meet_link(args: dict[str, Any]) -> Any:
    """Google Calendar SILENTLY IGNORES `conferenceData` on events.insert
    unless the request also carries `conferenceDataVersion=1` as a QUERY
    PARAMETER -- no error, no warning, just an ordinary event with no
    `hangoutLink`. This is the entire justification for this tool existing, so
    the query param is not optional."""
    token = google_api.resolve_mailbox_token(args.get("mailbox"))
    mailbox = args.get("mailbox") or "primary"
    body = _event_body(args)
    body["conferenceData"] = {
        "createRequest": {
            "requestId": str(uuid.uuid4()),
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    }
    return await google_api.post_json(
        google_api.API_CALENDAR,
        f"/calendars/{mailbox}/events",
        token=token,
        json=body,
        params={"conferenceDataVersion": 1},
    )


TOOLS: list[Tool] = [
    Tool(
        name="calendar_list_events",
        description="List events in a window.",
        inputSchema={
            "type": "object",
            "properties": {
                "timeMin": {"type": "string"},
                "timeMax": {"type": "string"},
                "mailbox": _EVENT_FIELDS["mailbox"],
            },
            "required": ["timeMin", "timeMax"],
        },
    ),
    Tool(
        name="calendar_get_event",
        description="Get one event by id.",
        inputSchema={
            "type": "object",
            "properties": {"eventId": {"type": "string"}, "mailbox": _EVENT_FIELDS["mailbox"]},
            "required": ["eventId"],
        },
    ),
    Tool(
        name="calendar_create_event",
        description="Create an event.",
        inputSchema={
            "type": "object",
            "properties": _EVENT_FIELDS,
            "required": ["summary", "start", "end"],
        },
    ),
    Tool(
        name="calendar_update_event",
        description=(
            "Update an existing event. `attendees` ADDS people to the meeting — "
            "everyone already invited stays invited, so there is no need to list them "
            "again. This tool cannot remove an attendee."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "eventId": {"type": "string"},
                **_EVENT_FIELDS,
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Email addresses to ADD to the event. Attendees already on it "
                        "are kept; nobody is removed."
                    ),
                },
            },
            "required": ["eventId", "summary", "start", "end"],
        },
    ),
    Tool(
        name="calendar_cancel_event",
        description="Cancel (delete) an event.",
        inputSchema={
            "type": "object",
            "properties": {"eventId": {"type": "string"}, "mailbox": _EVENT_FIELDS["mailbox"]},
            "required": ["eventId"],
        },
    ),
    Tool(
        name="calendar_respond_to_invite",
        description=(
            "Accept/decline/tentatively-accept an invite. "
            "`response` is one of accepted|declined|tentative."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "eventId": {"type": "string"},
                "response": {"type": "string", "enum": ["accepted", "declined", "tentative"]},
                "mailbox": _EVENT_FIELDS["mailbox"],
            },
            "required": ["eventId", "response"],
        },
    ),
    Tool(
        name="calendar_check_free_busy",
        description=(
            "Check free/busy in a window for the acting mailbox and, optionally, other "
            "people's calendars — use `calendars` to find a slot that works for several "
            "people at once. Each calendar answers separately in the response."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "timeMin": {"type": "string"},
                "timeMax": {"type": "string"},
                "mailbox": _EVENT_FIELDS["mailbox"],
                "calendars": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Additional calendar IDs (usually colleagues' email addresses) to "
                        "check alongside the acting mailbox."
                    ),
                },
            },
            "required": ["timeMin", "timeMax"],
        },
    ),
    Tool(
        name="calendar_create_meet_link",
        description=(
            "Create an event with a Google Meet link attached — this is the whole Meet "
            "integration; there is no separate Meet API."
        ),
        inputSchema={
            "type": "object",
            "properties": _EVENT_FIELDS,
            "required": ["summary", "start", "end"],
        },
    ),
]

CALL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "calendar_list_events": calendar_list_events,
    "calendar_get_event": calendar_get_event,
    "calendar_create_event": calendar_create_event,
    "calendar_update_event": calendar_update_event,
    "calendar_cancel_event": calendar_cancel_event,
    "calendar_respond_to_invite": calendar_respond_to_invite,
    "calendar_check_free_busy": calendar_check_free_busy,
    "calendar_create_meet_link": calendar_create_meet_link,
}
