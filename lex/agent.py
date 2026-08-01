"""The agent loop: messages -> LLM -> tool calls -> results -> repeat."""
import json
import logging
from types import SimpleNamespace

logger = logging.getLogger(__name__)


def run(client, model, messages, tools, max_iters=20, on_token=None) -> str:
    """One conversational turn. Appends to `messages` in place; returns final text.

    `on_token` is called with each text fragment as it streams in (only the
    text content, never tool-call args) — pass it to make replies render
    incrementally instead of appearing all at once. Omit it for the plain
    blocking call used by research passes and tests.
    """
    schemas = [{"type": "function", "function": t["schema"]}
               for t in tools.values()] or None
    for _ in range(max_iters):
        if on_token is None:
            msg = client.chat.completions.create(
                model=model, messages=messages, tools=schemas).choices[0].message
        else:
            msg = _stream(client, model, messages, schemas, on_token)
        if not msg.tool_calls:
            text = msg.content or ""
            messages.append({"role": "assistant", "content": text})
            return text
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": _call(tools, tc)})
    return "(stopped: hit max tool iterations — ask me to continue)"


def _stream(client, model, messages, schemas, on_token):
    """Consume an SSE stream, firing on_token per text delta, and return an
    object shaped like the non-streaming `.choices[0].message` (content +
    tool_calls) — tool-call fragments arrive keyed by index and have to be
    reassembled since a call's name/arguments can each split across chunks."""
    content = []
    calls: dict[int, dict] = {}
    for chunk in client.chat.completions.create(
            model=model, messages=messages, tools=schemas, stream=True):
        delta = chunk.choices[0].delta
        if delta.content:
            content.append(delta.content)
            on_token(delta.content)
        for tc in delta.tool_calls or []:
            slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            slot["id"] = slot["id"] or tc.id or ""
            if tc.function:
                slot["name"] += tc.function.name or ""
                slot["arguments"] += tc.function.arguments or ""
    tool_calls = [_tool_call_ns(v) for _, v in sorted(calls.items())]
    return SimpleNamespace(content="".join(content), tool_calls=tool_calls or None)


def _tool_call_ns(v: dict) -> SimpleNamespace:
    dump = {"id": v["id"], "type": "function",
            "function": {"name": v["name"], "arguments": v["arguments"]}}
    return SimpleNamespace(id=v["id"], function=SimpleNamespace(
        name=v["name"], arguments=v["arguments"]), model_dump=lambda: dump)


def _call(tools, tc) -> str:
    name = tc.function.name
    tool = tools.get(name)
    if tool is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        args = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"bad tool arguments: {e}"})
    try:
        return tool["handler"](args)
    except Exception as e:  # handlers shouldn't raise, but the loop never dies
        logger.exception("tool %s crashed", name)
        return json.dumps({"error": f"{name} crashed: {e}"})
