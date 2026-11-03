import json

import pytest

from goose_buddy import feed, state
from goose_buddy.cli import main
from goose_buddy.engine import Companion, generate_bones
from goose_buddy.events import Watcher
from goose_buddy.feed import FeedReader, emit, parse


@pytest.fixture
def path(tmp_path):
    return tmp_path / "feed.jsonl"


def test_emit_and_read_roundtrip_starting_from_now(path):
    emit("success", "before", path)
    r = FeedReader(path)
    assert r.read() == []                                   # the first look baselines at the end
    emit("all-green", "ci", path)
    emit("error", "build box", path)
    assert r.read() == [("all-green", "ci"), ("error", "build box")]
    assert r.read() == []


def test_unknown_reasons_are_refused_and_never_written(path):
    assert emit("rm -rf", "x", path) is False and not path.exists()
    for reason in ("turn", "idle", "pet", "hatch"):         # internal reasons are not for outsiders
        assert not emit(reason, "x", path)


def test_malformed_and_hostile_lines_are_dropped(path):
    r = FeedReader(path, from_start=True)
    junk = [b"not json", b"[1,2]", b'{"reason": 5}', b'{"reason": "nope"}', b'{"reason": ["error"]}',
            b'{"reason":"error","source":' + b'"' + b"x" * 600 + b'"}']
    path.write_bytes(b"\n".join(junk) + b'\n{"reason":"commit","source":{"a":1}}\n')
    assert r.read() == [("commit", "")] and r.dropped == len(junk)
    assert parse(b'{"reason":"error","source":"' + b"y" * 100 + b'"}')[1] == "y" * feed.MAX_SOURCE


def test_partial_line_waits_for_the_writer(path):
    r = FeedReader(path, from_start=True)
    path.write_bytes(b'{"reason":"er')
    assert r.read() == []
    with open(path, "ab") as f:
        f.write(b'ror","source":"s"}\n')
    assert r.read() == [("error", "s")]


def test_truncation_restarts_the_reader(path):
    r = FeedReader(path, from_start=True)
    for _ in range(3):
        emit("error", "", path)
    assert len(r.read()) == 3
    path.write_text("")
    emit("push", "", path)
    assert r.read() == [("push", "")]


def test_writer_rotates_big_files(path, monkeypatch):
    monkeypatch.setattr(feed, "MAX_FILE", 200)
    for _ in range(20):
        emit("success", "x" * 30, path)
    assert path.stat().st_size < 400
    assert all(parse(l.encode()) for l in path.read_text().splitlines())


def test_unwritable_location_is_not_an_exception(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert emit("error", "x", blocker / "feed.jsonl") is False


def test_the_watcher_reacts_to_feed_events_and_the_label_stays_out_of_the_bubble(path, tmp_path):
    clock = [1000.0]
    w = Watcher(Companion(generate_bones("feed-test"), "Honk", "p", 0, "x"), db=tmp_path / "none.db", feed_path=path,
                clock=lambda: clock[0], rng=lambda: 0.0)
    assert w.poll() == []                                    # baseline
    emit("all-green", "SECRET-LABEL-42", path)
    shown = w.poll()
    assert [s["reason"] for s in shown] == ["all-green"]
    assert "SECRET-LABEL-42" not in json.dumps(shown) and state.counters()["all_green"] == 1


def test_a_label_is_never_sent_to_the_model(path, monkeypatch, tmp_path):
    import httpx
    from goose_buddy.brain import Brain
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")
    seen = []

    def handler(req):
        seen.append(req.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "honk. nice."}}]})
    clock = [1000.0]
    w = Watcher(Companion(generate_bones("feed-test"), "Honk", "p", 0, "x"), db=tmp_path / "none.db", feed_path=path,
                brain=Brain(httpx.MockTransport(handler)), clock=lambda: clock[0], rng=lambda: 0.5)
    w.poll()
    emit("success", "CANARY-SOURCE-LABEL", path)
    w.poll()
    assert seen and "CANARY" not in seen[0]


def test_cli_emit(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("GOOSE_BUDDY_FEED", str(tmp_path / "f.jsonl"))
    assert main(["emit", "deploy", "--source", "ci"]) == 0
    assert main(["emit", "bogus"]) == 2
    assert json.loads((tmp_path / "f.jsonl").read_text())["source"] == "ci"


def test_events_written_before_the_file_existed_at_first_look_are_not_lost(path):
    r = FeedReader(path)
    assert r.read() == []                                   # no file yet
    emit("error", "first ever", path)
    assert r.read() == [("error", "first ever")]
