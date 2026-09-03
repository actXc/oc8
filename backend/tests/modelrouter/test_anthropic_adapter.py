"""_to_anthropic_messages -- building Anthropic's {"role": ..., "content": ...}
dicts from NeutralMessage, including the list-of-parts form used for images.

Plain-string content must keep working unchanged (the majority of existing
call sites); the list form is new and only produces Anthropic image blocks
for ImagePart entries.
"""

from __future__ import annotations

import base64

from oc8.modelrouter.adapters.anthropic import _to_anthropic_messages
from oc8.modelrouter.types import ImagePart, NeutralMessage, TextPart


def test_anthropic_adapter_builds_image_content_block() -> None:
    msg = NeutralMessage(
        role="user",
        content=[
            TextPart(text="What's in this image?"),
            ImagePart(data=b"\x89PNG...", content_type="image/png"),
        ],
    )
    _, messages = _to_anthropic_messages([msg])
    built = messages[0]
    assert built["role"] == "user"
    assert built["content"][0] == {"type": "text", "text": "What's in this image?"}
    assert built["content"][1]["type"] == "image"
    assert built["content"][1]["source"]["type"] == "base64"
    assert built["content"][1]["source"]["media_type"] == "image/png"
    assert built["content"][1]["source"]["data"] == base64.b64encode(b"\x89PNG...").decode()


def test_a_plain_string_user_message_is_unaffected() -> None:
    msg = NeutralMessage(role="user", content="hi there")
    _, messages = _to_anthropic_messages([msg])
    assert messages[0] == {"role": "user", "content": "hi there"}


def test_a_plain_string_system_message_is_unaffected() -> None:
    msg = NeutralMessage(role="system", content="be nice")
    system, messages = _to_anthropic_messages([msg])
    assert system == "be nice"
    assert messages == []


def test_an_assistant_turn_with_image_parts_becomes_content_blocks() -> None:
    msg = NeutralMessage(
        role="assistant",
        content=[
            TextPart(text="here you go"),
            ImagePart(data=b"\x89PNG...", content_type="image/png"),
        ],
    )
    _, messages = _to_anthropic_messages([msg])
    built = messages[0]
    assert built["role"] == "assistant"
    assert built["content"][0] == {"type": "text", "text": "here you go"}
    assert built["content"][1]["type"] == "image"
    assert built["content"][1]["source"]["media_type"] == "image/png"


def test_a_plain_string_assistant_message_is_unaffected() -> None:
    msg = NeutralMessage(role="assistant", content="ok")
    _, messages = _to_anthropic_messages([msg])
    assert messages[0] == {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}


def test_a_tool_result_message_is_unaffected() -> None:
    msg = NeutralMessage(role="tool", content="42", tool_call_id="toolu_1")
    _, messages = _to_anthropic_messages([msg])
    assert messages[0] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"}],
    }
