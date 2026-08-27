"""Reassembling a whole CompletionResult from a stream of chunks.

engine.py's step loop and internal_agent.py's /step both still need one
complete answer per turn -- caching, salvage, and metering all act on the
whole thing. Streaming only changes how it arrives: this module is what turns
"tokens as they come" back into "the same shape the rest of the run pipeline
already expects", while also handing every text fragment to a live callback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from oc8.modelrouter.accumulate import accumulate_stream
from oc8.modelrouter.types import CompletionChunk, ToolCallDelta, Usage

pytestmark = pytest.mark.asyncio


async def _stream(*chunks: CompletionChunk) -> AsyncIterator[CompletionChunk]:
    for c in chunks:
        yield c


async def test_text_chunks_concatenate_in_order() -> None:
    result = await accumulate_stream(
        _stream(CompletionChunk(text="Hal"), CompletionChunk(text="lo"))
    )
    assert result.text == "Hallo"


async def test_on_text_is_called_with_each_fragment_as_it_arrives() -> None:
    seen: list[str] = []

    async def on_text(fragment: str) -> None:
        seen.append(fragment)

    await accumulate_stream(
        _stream(CompletionChunk(text="Hal"), CompletionChunk(text="lo")), on_text=on_text
    )
    assert seen == ["Hal", "lo"]


async def test_on_text_is_not_called_for_a_chunk_with_no_text() -> None:
    """A tool-call-only or usage-only chunk must not emit an empty live delta."""
    seen: list[str] = []

    async def on_text(fragment: str) -> None:
        seen.append(fragment)

    await accumulate_stream(
        _stream(CompletionChunk(tool_calls=[ToolCallDelta(index=0, name="t")])), on_text=on_text
    )
    assert seen == []


async def test_tool_call_argument_fragments_merge_by_index() -> None:
    result = await accumulate_stream(
        _stream(
            CompletionChunk(
                tool_calls=[
                    ToolCallDelta(index=0, id="c1", name="create_record", arguments_fragment='{"mo')
                ]
            ),
            CompletionChunk(tool_calls=[ToolCallDelta(index=0, arguments_fragment='del":1}')]),
        )
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "c1"
    assert result.tool_calls[0].name == "create_record"
    assert result.tool_calls[0].arguments == {"model": 1}


async def test_two_parallel_tool_calls_do_not_cross_contaminate() -> None:
    result = await accumulate_stream(
        _stream(
            CompletionChunk(
                tool_calls=[
                    ToolCallDelta(index=0, id="c1", name="a", arguments_fragment="{}"),
                    ToolCallDelta(index=1, id="c2", name="b", arguments_fragment='{"x":'),
                ]
            ),
            CompletionChunk(tool_calls=[ToolCallDelta(index=1, arguments_fragment="1}")]),
        )
    )
    assert [tc.id for tc in result.tool_calls] == ["c1", "c2"]
    assert result.tool_calls[0].arguments == {}
    assert result.tool_calls[1].arguments == {"x": 1}


async def test_a_tool_call_with_no_argument_fragments_defaults_to_an_empty_object() -> None:
    """A no-arg tool may stream id/name and never emit a single input_json_delta
    -- an empty accumulated string must parse as {}, not raise."""
    result = await accumulate_stream(
        _stream(CompletionChunk(tool_calls=[ToolCallDelta(index=0, id="c1", name="ping")]))
    )
    assert result.tool_calls[0].arguments == {}


async def test_the_most_recently_reported_usage_wins() -> None:
    """Providers report usage as a cumulative total, not an increment -- summing
    chunk usages would double count."""
    result = await accumulate_stream(
        _stream(
            CompletionChunk(usage=Usage(tokens_in=10, tokens_out=1)),
            CompletionChunk(usage=Usage(tokens_in=10, tokens_out=7)),
        )
    )
    assert result.usage == Usage(tokens_in=10, tokens_out=7)


async def test_stop_reason_and_provider_model_are_carried_from_whichever_chunk_set_them() -> None:
    result = await accumulate_stream(
        _stream(
            CompletionChunk(text="hi", provider="anthropic", model="claude"),
            CompletionChunk(stop_reason="stop"),
        )
    )
    assert result.stop_reason == "stop"
    assert result.provider == "anthropic"
    assert result.model == "claude"


async def test_an_empty_stream_yields_a_default_result_not_an_error() -> None:
    result = await accumulate_stream(_stream())
    assert result.text == ""
    assert result.tool_calls == []
    assert result.stop_reason == "stop"
