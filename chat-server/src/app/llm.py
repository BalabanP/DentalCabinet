"""Streaming LLM call through the OpenAI SDK against Anthropic's OpenAI-compatible endpoint."""

import uuid
from typing import Iterator

from openai import NOT_GIVEN, OpenAI


def llm_api_call_streamed(model: str, base_url: str, messages: list[dict], api_key: str,
                          tools: list[dict] | None = None) -> Iterator[dict]:
    """Yield {"type": "text", "delta": str} while streaming, then one {"type": "message", "message": dict}.

    The final assistant message is in OpenAI chat format (content and/or tool_calls), ready to be
    appended to the conversation.
    """
    client = OpenAI(api_key=api_key, base_url=base_url)
    stream = client.chat.completions.create(
        model=model, messages=messages, tools=tools or NOT_GIVEN, stream=True
    )

    text = ""
    calls: dict[int, dict] = {}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text += delta.content
            yield {"type": "text", "delta": delta.content}
        for tc in delta.tool_calls or []:
            slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

    message: dict = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": c["id"] or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
            }
            for _, c in sorted(calls.items())
        ]
    yield {"type": "message", "message": message}
