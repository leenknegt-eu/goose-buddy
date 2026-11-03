import json
import threading
import time

from goose_buddy import state


def test_concurrent_counters_do_not_lose_updates():
    threads = [threading.Thread(target=lambda: [state.bump("x") for _ in range(25)]) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state.counters()["x"] == 200


def test_config_merges_nested_defaults_and_persists():
    state.save_config(brain={"minInterval": 99}, muted=True)
    cfg = state.load_config()
    assert cfg["brain"]["minInterval"] == 99 and cfg["brain"]["model"] == "beam-free" and cfg["muted"] is True


def test_reaction_expires_after_its_ttl():
    state.save_reaction("hi", "pet", "canned")
    assert state.live_reaction()["reaction"] == "hi"
    assert state.live_reaction(ttl=-1) is None


def test_corrupt_files_do_not_crash_readers(buddy_home):
    state.home()
    (buddy_home / "reaction.json").write_text("{not json")
    (buddy_home / "config.json").write_text("")
    assert state.load_reaction() is None and state.load_config()["brain"]["model"] == "beam-free"


def test_no_api_key_is_ever_written_to_state(monkeypatch, buddy_home):
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-should-not-appear")
    state.save_config(muted=False)
    state.bump("a")
    state.save_reaction("x", "y", "brain")
    assert all("sk-should-not-appear" not in p.read_text() for p in buddy_home.iterdir() if p.is_file())
