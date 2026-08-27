"""Anthropic/OpenAI provider-side cost reporting (Cost Center design,
2026-08-19 spec Part C). Requires a separate Admin/Org API key from the
regular completion key -- see modelrouter/keys.py's model_admin_key_ref().
Daily granularity only; this module never attempts per-request or
per-agent attribution, because the providers don't offer it. Mistral has no
such API and has no fetcher here.

The response shapes below are verified against each provider's live API
reference docs (checked 2026-08-19), not just the plan's best-effort guess:

- Anthropic `cost_report` (https://platform.claude.com/docs/en/api/admin/cost_report/retrieve):
  each `results[]` item's `amount` is a decimal STRING ("123.78912") in
  lowest currency units, with `currency` ("USD") as a SIBLING field -- NOT
  a nested `{value, currency}` object. The plan's mocked test had this
  wrong; fixed here.
- OpenAI `costs` (https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs):
  each `results[]` item's `amount` IS a nested `{value: <number>, currency:
  "usd"}` object, and bucket `start_time`/`end_time` are unix seconds. The
  plan's guess for OpenAI matches reality.

Both endpoints paginate (`has_more` / `next_page` -> `page` query param,
confirmed for both in their docs/cookbook). Both fetchers follow every page
so a multi-week backfill is never silently truncated by a provider's
default per-page bucket limit (Anthropic's usage-report docs cite a 7
bucket default / 31 bucket max for 1d granularity; OpenAI's default is
similarly small) -- we ask for the exact number of days needed, capped at
each provider's documented per-page maximum, and keep paging.
"""

from __future__ import annotations

import datetime as dt

import httpx

from oc8.modelrouter.http_errors import raise_for_status_with_body

_ANTHROPIC_COST_URL = "https://api.anthropic.com/v1/organizations/cost_report"
_OPENAI_COST_URL = "https://api.openai.com/v1/organization/costs"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_MAX_LIMIT = 31  # 1d bucket_width cap per the Usage & Cost Admin API docs
_OPENAI_MAX_LIMIT = 180  # 1d bucket_width cap per the Costs API docs


def _rfc3339_midnight(day: dt.date) -> str:
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC).isoformat().replace("+00:00", "Z")


def _bucket_limit(since: dt.date, until: dt.date, cap: int) -> int:
    return min((until - since).days + 1, cap)


async def fetch_anthropic_cost(
    admin_key: str, *, since: dt.date, until: dt.date
) -> dict[dt.date, int]:
    headers = {"x-api-key": admin_key, "anthropic-version": _ANTHROPIC_VERSION}
    params: dict[str, str | int] = {
        "starting_at": _rfc3339_midnight(since),
        # ending_at is exclusive, so the bucket for `until` itself is included.
        "ending_at": _rfc3339_midnight(until + dt.timedelta(days=1)),
        "limit": _bucket_limit(since, until, _ANTHROPIC_MAX_LIMIT),
    }
    out: dict[dt.date, int] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        page: str | None = None
        while True:
            if page is not None:
                params["page"] = page
            resp = await client.get(_ANTHROPIC_COST_URL, headers=headers, params=params)
            raise_for_status_with_body(resp)
            body = resp.json()
            for bucket in body.get("data", []):
                day = dt.datetime.fromisoformat(bucket["starting_at"].replace("Z", "+00:00")).date()
                # Anthropic's `amount` is a decimal string in the LOWEST currency
                # unit (cents for USD), per the live Cost Report API docs -- e.g.
                # "123.45" USD represents $1.23, not $123.45. 1 cent = $0.01 =
                # 10_000 micro-dollars (1 USD = 1_000_000 micros).
                usd_cents = sum(float(r["amount"]) for r in bucket.get("results", []))
                out[day] = out.get(day, 0) + round(usd_cents * 10_000)
            if not body.get("has_more") or body.get("next_page") is None:
                break
            page = body["next_page"]
    return out


async def fetch_openai_cost(
    admin_key: str, *, since: dt.date, until: dt.date
) -> dict[dt.date, int]:
    headers = {"Authorization": f"Bearer {admin_key}"}
    _end = dt.datetime.combine(until + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
    params: dict[str, str | int] = {
        "start_time": int(dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC).timestamp()),
        # end_time is exclusive, mirroring Anthropic's ending_at semantics.
        "end_time": int(_end.timestamp()),
        "bucket_width": "1d",
        "limit": _bucket_limit(since, until, _OPENAI_MAX_LIMIT),
    }
    out: dict[dt.date, int] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        page: str | None = None
        while True:
            if page is not None:
                params["page"] = page
            resp = await client.get(_OPENAI_COST_URL, headers=headers, params=params)
            raise_for_status_with_body(resp)
            body = resp.json()
            for bucket in body.get("data", []):
                day = dt.datetime.fromtimestamp(bucket["start_time"], tz=dt.UTC).date()
                usd = sum(float(r["amount"]["value"]) for r in bucket.get("results", []))
                out[day] = out.get(day, 0) + round(usd * 1_000_000)
            if not body.get("has_more") or body.get("next_page") is None:
                break
            page = body["next_page"]
    return out
