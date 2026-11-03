import json
import sqlite3

import pytest

from goose_buddy import state
from goose_buddy.engine import Companion, generate_bones
from goose_buddy.events import Watcher


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    con = sqlite3.connect(path)
    con.execute("create table messages (id integer primary key, session_id text, role text, content_json text)")
    con.commit()
    con.close()
    return path


def add(db, role, *parts):
    con = sqlite3.connect(db)
    con.execute("insert into messages (session_id, role, content_json) values ('s', ?, ?)", (role, json.dumps(list(parts))))
    con.commit()
    con.close()


def tool(text, error=False):
    return {"type": "toolResponse", "id": "1", "toolResult": {"status": "success", "value": {"isError": error, "content": [{"type": "text", "text": text}]}}}


def make(db, clock):
    b = generate_bones("watch-test")
    return Watcher(Companion(b, "Honk", "p", 0, "x"), db=db, clock=lambda: clock[0], rng=lambda: 0.0)


def test_history_is_never_replayed(db):
    add(db, "user", tool("2 failed"))
    w = make(db, [1000.0])
    assert w.poll() == []                      # first run baselines at the current end
    add(db, "user", tool("FAILED tests/x.py - 1 failed"))
    assert [r["reason"] for r in w.poll()] == ["test-fail"]


def test_position_survives_a_restart(db):
    clock = [1000.0]
    w = make(db, clock)
    w.poll()
    add(db, "user", tool("1 failed"))
    assert len(w.poll()) == 1
    clock[0] += 100
    again = make(db, clock)                    # a new process: must not re-react to the old row
    assert again.poll() == []
    add(db, "user", tool("All 9 tests passed"))
    assert [r["reason"] for r in again.poll()] == ["all-green"]


def test_cooldown_throttles_bubbles_but_counters_still_count(db):
    clock = [1000.0]
    w = make(db, clock)
    w.poll()
    add(db, "user", tool("1 failed"))
    add(db, "user", tool("2 failed"))
    add(db, "user", tool("3 failed"))
    assert len(w.poll()) == 1                  # one bubble inside the 30s cooldown
    assert state.counters()["tests_failed"] == 3
    clock[0] += 31
    add(db, "user", tool("4 failed"))
    assert len(w.poll()) == 1


def test_unrecognised_failures_still_register_as_errors(db):
    w = make(db, [1000.0])
    w.poll()
    add(db, "user", tool("something odd happened", error=True))
    assert [r["reason"] for r in w.poll()] == ["error"]


def test_addressing_the_buddy_by_name_gets_a_reply(db):
    w = make(db, [1000.0])
    w.poll()
    add(db, "user", {"type": "text", "text": "hey honk, what do you think?"})
    r = w.poll()
    assert r and r[0]["reason"] == "name" and r[0]["source"] == "canned"


def test_credit_exhaustion_notification_is_noticed(db):
    w = make(db, [1000.0])
    w.poll()
    add(db, "assistant", {"type": "systemNotification", "notificationType": "creditsExhausted", "msg": "x"})
    assert [r["reason"] for r in w.poll()] == ["credits"]


def test_muted_buddy_stays_quiet(db):
    state.save_config(muted=True)
    w = make(db, [1000.0])
    w.poll()
    add(db, "user", tool("1 failed"))
    assert w.poll() == []


def test_error_streak_escalates(db):
    clock = [1000.0]
    w = make(db, clock)
    w.poll()
    out = []
    for i in range(3):
        clock[0] += 31
        add(db, "user", tool(f"{i + 1} failed"))
        out += w.poll()
    assert out[-1]["reaction"].startswith("HONK") or "three" in out[-1]["reaction"]


def test_a_missing_database_is_not_an_error(tmp_path):
    w = make(tmp_path / "nope.db", [1000.0])
    assert w.poll() == []


def test_watching_feeds_xp_mood_achievements_and_suggestions(db):
    from goose_buddy import progress
    clock = [1000.0]
    w = make(db, clock)
    w.poll()                                           # baseline
    for i in range(3):                                 # the same error three times
        add(db, "user", tool("Traceback (most recent call last):\nValueError: bad thing", error=True))
        clock[0] += 60
        shown = w.poll()
    assert shown and shown[-1]["reason"] == "suggestion"          # repeated-error nudge took the bubble
    assert progress.get_mood(clock[0])["current"] == "chaotic"    # 3 errors inside 5 minutes
    assert "bug_spotter" in progress.unlocked_ids()
    assert progress.xp_state()["totalXp"] >= 3 * 15 + 50
    assert state.load_reaction()["reason"] == "suggestion"


def test_a_pet_or_show_does_not_count_as_a_new_session(db):
    make(db, [1000.0])
    assert state.counters().get("sessions", 0) == 0
    Watcher(Companion(generate_bones("x"), "H", "p", 0, "x"), db=db, new_session=True)
    assert state.counters()["sessions"] == 1


def test_a_new_pane_skips_what_happened_while_it_was_closed(db):
    w = make(db, [1000.0])
    w.poll()
    add(db, "user", tool("2 failed"))
    fresh = Watcher(Companion(generate_bones("x"), "H", "p", 0, "x"), db=db, clock=lambda: 2000.0, new_session=True)
    assert fresh.poll() == []                          # the failure happened before this pane opened
