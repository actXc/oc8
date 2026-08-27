"""Neutral interpreters for connection-declared tool semantics.

The core must stay software-agnostic: it names no product, model, or field. But
two things about a tool call ARE software-specific — where the monetary value
lives (for the approval threshold), and how to describe which record a call
touches (for the live log). A connection whose tools need that supplies a
declarative spec in its config; the plugin that created the connection owns
those specs, so all specifics live in the plugin (data), never here.

Both functions are pure and generic: given a spec (arbitrary keys/paths) and a
tool call's arguments, they compute a value / a description. They contain no
knowledge of any particular software.
"""

from __future__ import annotations

import json
from typing import Any


def _num(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.replace("€", "").replace(",", "").strip())
        except ValueError:
            return None
    return None


def _dig(obj: Any, path: list[str]) -> Any:
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _line_total(line: dict[str, Any], price_field: str, qty_field: str) -> float | None:
    price = _num(line.get(price_field))
    if price is None:
        return None
    qty = _num(line.get(qty_field))
    return price * (qty if qty is not None else 1.0)


def extract_value(
    arguments: dict[str, Any], value_spec: dict[str, Any] | None = None
) -> float | None:
    """The value implied by a tool call, or None — driven ENTIRELY by the spec.

    The core makes no assumption that a tool call carries a value (not even a
    monetary one). Only a `value_spec`, declared by the connection's plugin,
    says where the value is:
    - `direct_fields`: argument keys that hold a number (scanned recursively);
    - `line_items`: {path, price_field, qty_field} to value a list of line-item
      dicts (or [cmd, id, vals] command tuples) as sum(price * qty).

    No spec (or an empty one) ⇒ None ⇒ no value-based approval for that call.
    The spec's keys/paths are opaque strings supplied by a plugin; this function
    knows nothing about what software or use case they belong to.
    """
    spec = value_spec or {}
    keys = set(spec.get("direct_fields") or [])
    found: list[float] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in keys:
                    n = _num(val)
                    if n is not None:
                        found.append(n)
                walk(val, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item, depth + 1)

    walk(arguments)

    line_items = spec.get("line_items")
    if isinstance(line_items, dict):
        path = line_items.get("path") or []
        price_field = str(line_items.get("price_field", "price"))
        qty_field = str(line_items.get("qty_field", "quantity"))
        rows = _dig(arguments, [str(p) for p in path]) if isinstance(path, list) else None
        if isinstance(rows, (list, tuple)):
            total = 0.0
            summed = False
            for item in rows:
                vals: Any = None
                if isinstance(item, (list, tuple)) and len(item) == 3 and isinstance(item[2], dict):
                    vals = item[2]  # a (command, id, values) tuple
                elif isinstance(item, dict):
                    vals = item
                if vals is not None:
                    lt = _line_total(vals, price_field, qty_field)
                    if lt is not None:
                        total += lt
                        summed = True
            if summed:
                found.append(total)

    return max(found) if found else None



def _record_ref(arguments: dict[str, Any], focus_spec: dict[str, Any]) -> str:
    """`#43`, or `'Some name'`, or empty when the call names no particular record."""
    for id_key in focus_spec.get("id_fields") or []:
        rid = arguments.get(str(id_key))
        if rid:
            return f"#{rid}"
    name_path = focus_spec.get("name_path")
    if isinstance(name_path, list):
        name = _dig(arguments, [str(p) for p in name_path])
        if name:
            return repr(str(name))
    return ""


def describes_a_record(
    tool_name: str, arguments: dict[str, Any], focus_spec: dict[str, Any] | None
) -> bool:
    """Whether this call names a PARTICULAR record, rather than a kind of them.

    A search does not: "Durchsucht Ticket" is the same sentence whatever the
    queue holds. Task cards use this to keep the ticket a run actually worked
    instead of letting a later search overwrite it with nothing.
    """
    if not focus_spec:
        return False
    if tool_name in (focus_spec.get("search_tools") or []):
        return False
    if not _entity_of(tool_name, arguments, focus_spec):
        return False
    return bool(_record_ref(arguments, focus_spec))


def _entity_of(
    tool_name: str, arguments: dict[str, Any], focus_spec: dict[str, Any]
) -> str:
    """Which kind of thing a call is about.

    Two shapes, because software differs and the core may assume neither:

    * `entity_field` -- the entity is an ARGUMENT. Systems with one generic
      endpoint per operation work this way ("update this record, of this kind").
    * `tool_entities` -- the entity is implied by the TOOL. Systems with one
      endpoint per kind work this way: `create_issue` says "issue" in its name
      and takes no field that repeats it.

    Without the second shape a whole system's calls produce no live-log line at
    all, which is the failure that once left a run's log blank for an hour.
    """
    field = str(focus_spec.get("entity_field", ""))
    if field:
        named = str(arguments.get(field, ""))
        if named:
            return named
    by_tool = focus_spec.get("tool_entities") or {}
    if isinstance(by_tool, dict):
        return str(by_tool.get(tool_name, ""))
    return ""


def call_entity(
    tool_name: str, arguments: dict[str, Any], focus_spec: dict[str, Any] | None
) -> str:
    """Which kind of thing a call is about, or empty. The public form of the
    resolution `describe_focus`/`record_identity` already use internally, so
    every reader of a `focus_spec` answers this question the same way -- both
    declared shapes (`entity_field` and `tool_entities`), not just one."""
    if not focus_spec:
        return ""
    return _entity_of(tool_name, arguments, focus_spec)


def describe_focus(
    tool_name: str, arguments: dict[str, Any], focus_spec: dict[str, Any] | None
) -> str | None:
    """A human live-log line for the record a tool call touches, or None.

    Driven entirely by `focus_spec` (supplied by a plugin):
    - `entity_field`: argument key naming the entity type (e.g. a model);
    - `tool_entities`: tool name -> entity, for software whose tools ARE the
      entity (`create_issue`) and carry no such argument;
    - `labels`: entity value -> human label (only listed entities are described);
    - `id_fields`: argument keys that may hold the record id;
    - `name_path`: path to a human name in the arguments;
    - `verbs`: tool_name -> verb; `default_verb` otherwise;
    - `search_tools`: tool names treated as a search over the entity.

    Returns None when there is no spec, or the call's entity is not labelled.
    """
    if not focus_spec:
        return None
    entity = _entity_of(tool_name, arguments, focus_spec)
    labels = focus_spec.get("labels") or {}
    if not isinstance(labels, dict) or entity not in labels:
        return None
    label = str(labels[entity])

    if tool_name in (focus_spec.get("search_tools") or []):
        return f"{focus_spec.get('search_label', 'Durchsucht')} {label}"

    ref = _record_ref(arguments, focus_spec)

    verbs = focus_spec.get("verbs") or {}
    verb = str(verbs.get(tool_name, focus_spec.get("default_verb", "")))
    return f"{verb} {label} {ref}".strip()


def record_identity(
    tool_name: str, arguments: dict[str, Any], focus_spec: dict[str, Any] | None
) -> tuple[str, str] | None:
    """(entity, reference) when a call names ONE particular record, else None.

    The identity two agents can collide on, and the unit a run's blast radius is
    counted in. A search names a kind of record rather than one, so it has no
    identity here -- two agents reading the same queue is not a conflict, it is
    how a queue works.
    """
    if not focus_spec:
        return None
    if tool_name in (focus_spec.get("search_tools") or []):
        return None
    entity = _entity_of(tool_name, arguments, focus_spec)
    ref = focus_ref_id(arguments, focus_spec)
    if not entity or not ref:
        return None
    return entity, ref


def record_title(result: str, ref_id: str, focus_spec: dict[str, Any] | None) -> str | None:
    """The human name of the record a call just touched, read from its RESULT.

    The subject is not in the arguments -- an update carries an id and the fields
    to change -- so a card could only ever say "Ticket #55". It IS in what the
    call returns, and reading it here keeps core ignorant of the system behind
    the connection: the plugin says which key holds the id and which hold a
    title, core only walks the JSON looking for a record with THAT id.

    Matching on the id matters: a search returns many records, and taking the
    first name would label the card with a ticket the run never touched.

    Returns None for anything unparseable. A label is cosmetic and must never
    cost a call.
    """
    if not focus_spec or not ref_id:
        return None
    id_field = str(focus_spec.get("id_field") or "")
    title_fields = [str(f) for f in (focus_spec.get("title_fields") or [])]
    if not id_field or not title_fields:
        return None
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        return None

    stack: list[Any] = [parsed]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if str(node.get(id_field, "")) == str(ref_id):
                for field in title_fields:
                    value = node.get(field)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def focus_ref_id(arguments: dict[str, Any], focus_spec: dict[str, Any] | None) -> str:
    """The bare record id a call names, or empty. `describe_focus` renders it as
    `#43`; this is the same value without the decoration, for looking a title up."""
    if not focus_spec:
        return ""
    ref = _record_ref(arguments, focus_spec)
    return ref[1:] if ref.startswith("#") else ""
