import json
from lex import memory, sessions


def test_memory_roundtrip(lex_home_tmp):
    assert memory.read() == ""
    out = json.loads(memory.handle_memory_save({"note": "prefers concise answers"}))
    assert out["success"]
    assert "prefers concise answers" in memory.read()
    memory.handle_memory_save({"note": "moderate risk"})
    assert memory.read().count("\n- ") + memory.read().startswith("- ") >= 2


def test_session_roundtrip(lex_home_tmp):
    p = sessions.new_session()
    sessions.append(p, {"role": "user", "content": "hi"})
    sessions.append(p, {"role": "assistant", "content": "hello"})
    assert sessions.load(p) == [{"role": "user", "content": "hi"},
                                {"role": "assistant", "content": "hello"}]


def test_list_sessions_newest_first(lex_home_tmp):
    a, b = sessions.new_session(), sessions.new_session()
    got = sessions.list_sessions()
    assert got[0] >= got[-1]
    assert set(got) >= {a, b}
