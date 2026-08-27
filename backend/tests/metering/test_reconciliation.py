"""Anthropic/OpenAI cost-report fetchers -- admin-key auth, daily granularity.
Mocked HTTP, no real network calls in tests.

The mocked response shapes here match each provider's live API reference docs
(checked 2026-08-19), not the plan's original best-effort guess:
- Anthropic `cost_report`: `results[].amount` is a decimal STRING with
  `currency` as a sibling field (NOT a nested `{value, currency}` object --
  the plan's draft had this wrong). See
  https://platform.claude.com/docs/en/api/admin/cost_report/retrieve
- OpenAI `costs`: `results[].amount` IS a nested `{value, currency}` object,
  confirmed against
  https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest

from oc8.metering.reconciliation import fetch_anthropic_cost, fetch_openai_cost

pytestmark = pytest.mark.asyncio


async def test_fetch_anthropic_cost_parses_daily_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        assert kwargs["headers"]["x-api-key"] == "admin-key-123"
        assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "starting_at": "2026-08-18T00:00:00Z",
                        "ending_at": "2026-08-19T00:00:00Z",
                        "results": [{"amount": "1.50", "currency": "USD"}],
                    },
                    {
                        "starting_at": "2026-08-19T00:00:00Z",
                        "ending_at": "2026-08-20T00:00:00Z",
                        "results": [{"amount": "2.25", "currency": "USD"}],
                    },
                ],
                "has_more": False,
                "next_page": None,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await fetch_anthropic_cost(
        "admin-key-123", since=dt.date(2026, 8, 18), until=dt.date(2026, 8, 19)
    )
    # "1.50"/"2.25" are cents, per the live Anthropic Cost Report API docs --
    # 1 cent = 10_000 micro-dollars.
    assert result[dt.date(2026, 8, 18)] == 15_000
    assert result[dt.date(2026, 8, 19)] == 22_500


async def test_fetch_anthropic_cost_sums_multiple_results_per_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`group_by` (or plain multi-line-item days) can return more than one
    result per time bucket -- they all belong to the same report_date."""

    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "starting_at": "2026-08-18T00:00:00Z",
                        "ending_at": "2026-08-19T00:00:00Z",
                        "results": [
                            {"amount": "1.00", "currency": "USD"},
                            {"amount": "0.50", "currency": "USD"},
                        ],
                    }
                ],
                "has_more": False,
                "next_page": None,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await fetch_anthropic_cost(
        "admin-key-123", since=dt.date(2026, 8, 18), until=dt.date(2026, 8, 18)
    )
    # "1.00" + "0.50" = 1.50 cents -> 15_000 micro-dollars.
    assert result[dt.date(2026, 8, 18)] == 15_000


async def test_fetch_anthropic_cost_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        calls.append(dict(kwargs["params"]))
        if kwargs["params"].get("page") is None:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "starting_at": "2026-08-18T00:00:00Z",
                            "ending_at": "2026-08-19T00:00:00Z",
                            "results": [{"amount": "1.00", "currency": "USD"}],
                        }
                    ],
                    "has_more": True,
                    "next_page": "page_abc",
                },
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "starting_at": "2026-08-19T00:00:00Z",
                        "ending_at": "2026-08-20T00:00:00Z",
                        "results": [{"amount": "2.00", "currency": "USD"}],
                    }
                ],
                "has_more": False,
                "next_page": None,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await fetch_anthropic_cost(
        "admin-key-123", since=dt.date(2026, 8, 18), until=dt.date(2026, 8, 19)
    )
    # "1.00"/"2.00" are cents -> 10_000/20_000 micro-dollars.
    assert result == {dt.date(2026, 8, 18): 10_000, dt.date(2026, 8, 19): 20_000}
    assert len(calls) == 2
    assert calls[1]["page"] == "page_abc"


async def test_fetch_openai_cost_parses_daily_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        assert kwargs["headers"]["Authorization"] == "Bearer admin-key-456"
        return httpx.Response(
            200,
            json={
                "object": "page",
                "data": [
                    {
                        "object": "bucket",
                        "start_time": 1787011200,
                        "end_time": 1787097600,
                        "results": [
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": 0.75, "currency": "usd"},
                                "line_item": None,
                                "project_id": None,
                            }
                        ],
                    }
                ],
                "has_more": False,
                "next_page": None,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await fetch_openai_cost(
        "admin-key-456", since=dt.date(2026, 8, 18), until=dt.date(2026, 8, 18)
    )
    assert list(result.values()) == [750_000]


async def test_fetch_openai_cost_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        calls.append(dict(kwargs["params"]))
        if kwargs["params"].get("page") is None:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "start_time": 1787011200,
                            "end_time": 1787097600,
                            "results": [{"amount": {"value": 1.0, "currency": "usd"}}],
                        }
                    ],
                    "has_more": True,
                    "next_page": "page_xyz",
                },
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "start_time": 1787097600,
                        "end_time": 1787184000,
                        "results": [{"amount": {"value": 2.0, "currency": "usd"}}],
                    }
                ],
                "has_more": False,
                "next_page": None,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await fetch_openai_cost(
        "admin-key-456", since=dt.date(2026, 8, 18), until=dt.date(2026, 8, 19)
    )
    assert result == {dt.date(2026, 8, 18): 1_000_000, dt.date(2026, 8, 19): 2_000_000}
    assert len(calls) == 2
    assert calls[1]["page"] == "page_xyz"


async def test_fetch_anthropic_cost_raises_with_body_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": "admin key lacks the required scope"}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await fetch_anthropic_cost(
            "admin-key-123", since=dt.date(2026, 8, 18), until=dt.date(2026, 8, 18)
        )
    assert "admin key lacks the required scope" in str(exc.value)
