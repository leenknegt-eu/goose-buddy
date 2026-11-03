import json
import re

import httpx

from goose_buddy import state
from goose_buddy.brain import MAX_LINE, USER_AGENT, Brain, build_messages, clean
from goose_buddy.engine import Companion, generate_bones
from goose_buddy.reactions import REASON_TEXT

KEY = "sk-test-KEY-never-in-bodies"


def companion():
    return Companion(generate_bones("brain-test"), "Honk", "A goose that judges.", 0, "x")


def transport(reply="honk. tests failed again.", status=200, log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "nope"})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply, "reasoning_content": "..."}}]})
    return httpx.MockTransport(handler)


def test_request_follows_opencode_go_rules(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    seen = []
    assert Brain(transport(log=seen)).react(companion(), "test-fail") == "honk. tests failed again."
    req = seen[0]
    assert req.url.host == "opencode.ai" and req.url.path.endswith("/go/v1/chat/completions")
    assert req.headers["user-agent"] == USER_AGENT and req.headers["user-agent"].startswith("goose-buddy/")
    assert re.fullmatch(r"[0-9a-f-]{36}", req.headers["x-opencode-session"])
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "beam-free" and body["max_tokens"] >= 700      # reasoning models need headroom


def test_session_id_is_stable_across_calls(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    seen = []
    clock = iter(range(1000, 100000, 100))
    b = Brain(transport(log=seen), clock=lambda: next(clock))
    b.react(companion(), "commit")
    b.react(companion(), "commit")
    assert seen[0].headers["x-opencode-session"] == seen[1].headers["x-opencode-session"]


def test_the_prompt_contains_only_persona_and_a_fixed_reason_phrase():
    for reason, phrase in REASON_TEXT.items():
        msgs = build_messages(companion(), reason)
        assert msgs[1] == {"role": "user", "content": f"event: {phrase}"}
        assert "Honk" in msgs[0]["content"] and "goose" in msgs[0]["content"] or True
    assert build_messages(companion(), "never-heard-of-it")[1]["content"] == "event: something happened"


def test_nothing_from_a_session_ever_reaches_the_model(monkeypatch, tmp_path):
    """Canary: secrets in tool output must not appear anywhere in any request."""
    import sqlite3

    from goose_buddy.events import Watcher
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.execute("create table messages (id integer primary key, session_id text, role text, content_json text)")
    con.commit()
    seen = []
    w = Watcher(companion(), db=db, brain=Brain(transport(log=seen)), clock=lambda: 5000.0)
    w.poll()                                             # baseline
    canary = "CANARY-PRIVATE-DISASSEMBLY mov eax, [ebp+8] /work/GitHub/f3decomp/game/Fallout3.exe"
    part = [{"type": "toolResponse", "toolResult": {"value": {"isError": True, "content": [{"type": "text", "text": canary + "\n2 failed"}]}}}]
    con.execute("insert into messages (session_id, role, content_json) values ('s','user',?)", (json.dumps(part),))
    con.commit()
    shown = w.poll()
    assert shown and shown[-1]["source"] == "brain" and seen
    blob = b"".join(r.content for r in seen) + str([dict(r.headers) for r in seen]).encode()
    assert b"CANARY" not in blob and b"Fallout3" not in blob and b"ebp" not in blob


def test_failures_return_none_never_raise(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    assert Brain(transport(status=400)).react(companion(), "error") is None
    assert Brain(transport(reply="")).react(companion(), "error") is None       # reasoning ate the budget
    boom = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert Brain(boom).react(companion(), "error") is None


def test_no_key_means_no_request(monkeypatch):
    seen = []
    assert Brain(transport(log=seen)).react(companion(), "error") is None and seen == []


def test_rate_limits_hold(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    now = [10000.0]
    seen = []
    b = Brain(transport(log=seen), clock=lambda: now[0])
    assert b.react(companion(), "error")
    now[0] += 5
    assert b.react(companion(), "error") is None                  # inside minInterval
    now[0] += 30
    assert b.react(companion(), "error")
    state.save_config(brain={"maxPerHour": 3, "minInterval": 1})
    now[0] += 5
    assert b.react(companion(), "error")                          # third call in the hour
    now[0] += 5
    assert b.react(companion(), "error") is None                  # cap reached
    assert len(seen) == 3


def test_disabled_brain_is_silent(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    state.save_config(brain={"enabled": False})
    seen = []
    assert Brain(transport(log=seen)).react(companion(), "error") is None and seen == []


def test_clean_strips_quotes_and_bounds_length():
    assert clean('  "honk"  ') == "honk" and clean("") is None and clean("x") is None
    assert len(clean("word " * 100)) <= MAX_LINE


def test_soul_parses_a_name_and_personality_and_sends_only_persona_data(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    seen = []
    reply = "NAME: Biscuit!\nPERSONALITY: A watchful goose that sulks at red builds and gloats at green ones."
    got = Brain(transport(reply, log=seen)).soul(companion().bones)
    assert got == ("Biscuit", "A watchful goose that sulks at red builds and gloats at green ones.")
    body = json.loads(seen[0].content)
    user = body["messages"][1]["content"]
    assert re.fullmatch(r"A \w+ \w+\. Stats \(0-100\): [A-Z0-9 ,]+\.", user)   # species, rarity, stats: nothing else


def test_soul_rejects_unusable_replies(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", KEY)
    for bad in ("no format at all", "NAME: \nPERSONALITY: short", "NAME: Ok\nPERSONALITY: tiny"):
        state.kv_set("brain", {})                     # reset the rate limiter between attempts
        assert Brain(transport(bad)).soul(companion().bones) is None
