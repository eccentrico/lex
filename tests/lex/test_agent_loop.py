import json
from types import SimpleNamespace as NS

from lex import agent


def _resp(content=None, tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


def _tc(id, name, arguments):
    return NS(id=id, type="function",
              function=NS(name=name, arguments=arguments),
              model_dump=lambda: {"id": id, "type": "function",
                                  "function": {"name": name, "arguments": arguments}})


class FakeClient:
    def __init__(self, responses):
        self._r = list(responses)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        return self._r.pop(0)


def _chunk(content=None, tool_call=None):
    """One SSE chunk: `tool_call` is (index, id, name, arguments) fragments."""
    tc = None
    if tool_call is not None:
        i, id_, name, args = tool_call
        tc = [NS(index=i, id=id_, function=NS(name=name, arguments=args))]
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tc))])


class FakeStreamClient:
    """Each entry in `streams` is a list of chunks for one create() call."""
    def __init__(self, streams):
        self._s = list(streams)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        assert kwargs.get("stream") is True
        return iter(self._s.pop(0))


def test_stream_fires_on_token_per_content_delta():
    c = FakeStreamClient([[_chunk("hel"), _chunk("lo")]])
    tokens = []
    msgs = [{"role": "user", "content": "hi"}]
    out = agent.run(c, "m", msgs, {}, on_token=tokens.append)
    assert out == "hello" and tokens == ["hel", "lo"]


def test_stream_reassembles_split_tool_call_then_answers():
    log = []
    c = FakeStreamClient([
        [_chunk(tool_call=(0, "1", "ec", None)),
         _chunk(tool_call=(0, None, "ho", '{"x"')),
         _chunk(tool_call=(0, None, None, ': 1}'))],
        [_chunk("done")],
    ])
    msgs = [{"role": "user", "content": "go"}]
    out = agent.run(c, "m", msgs, _tools(log), on_token=lambda t: None)
    assert out == "done" and log == [{"x": 1}]


def _tools(log):
    return {"echo": {"schema": {"name": "echo", "parameters": {}},
                     "handler": lambda a: log.append(a) or json.dumps({"echoed": a})}}


def test_plain_text_turn():
    c = FakeClient([_resp(content="hello")])
    msgs = [{"role": "user", "content": "hi"}]
    assert agent.run(c, "m", msgs, {}) == "hello"
    assert msgs[-1] == {"role": "assistant", "content": "hello"}


def test_tool_call_then_answer():
    log = []
    c = FakeClient([_resp(tool_calls=[_tc("1", "echo", '{"x": 1}')]),
                    _resp(content="done")])
    msgs = [{"role": "user", "content": "go"}]
    assert agent.run(c, "m", msgs, _tools(log)) == "done"
    assert log == [{"x": 1}]
    tool_msg = [m for m in msgs if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "1"


def test_unknown_tool_and_bad_json_dont_crash():
    c = FakeClient([_resp(tool_calls=[_tc("1", "nope", "{}"),
                                      _tc("2", "echo", "not json")]),
                    _resp(content="ok")])
    msgs = [{"role": "user", "content": "go"}]
    assert agent.run(c, "m", msgs, _tools([])) == "ok"
    errs = [json.loads(m["content"]) for m in msgs if m["role"] == "tool"]
    assert all("error" in e for e in errs)


def test_handler_exception_becomes_error_result():
    def bad(a):
        raise RuntimeError("boom")
    tools = {"bad": {"schema": {"name": "bad"}, "handler": bad}}
    c = FakeClient([_resp(tool_calls=[_tc("1", "bad", "{}")]), _resp(content="ok")])
    msgs = [{"role": "user", "content": "go"}]
    assert agent.run(c, "m", msgs, tools) == "ok"


def test_max_iters_stops():
    c = FakeClient([_resp(tool_calls=[_tc(str(i), "echo", "{}")]) for i in range(5)])
    out = agent.run(c, "m", [{"role": "user", "content": "go"}], _tools([]), max_iters=3)
    assert "stopped" in out
