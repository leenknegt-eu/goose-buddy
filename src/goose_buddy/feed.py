"""The event feed: how anything that is not a saved goose session can make the buddy react.

A feed is an append-only JSON-lines file (default `$GOOSE_BUDDY_HOME/feed.jsonl`, or `$GOOSE_BUDDY_FEED`):

    {"reason": "all-green", "source": "ci"}

`reason` must be one of the buddy's known reason codes (see `REASONS`); `source` is a short free-text
label. Anything can write one line: a build script, a CI poller, a hook, another MCP extension
(`goose-buddy emit all-green --source ci`, or just `echo '{...}' >> feed.jsonl`).

The feed is untrusted input. Lines are size-capped, parsed strictly, and unknown reasons are dropped.
Only the reason code ever influences the reaction; `source` is a label that is never sent to the model.
A new watcher starts at the end of the file, like it does for sessions.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from . import state
from .reactions import REASON_TEXT

#: reasons a feed may use: exactly the ones the buddy can describe to its brain
REASONS = frozenset(REASON_TEXT) - {"turn", "idle", "pet", "hatch"}
MAX_LINE = 512
MAX_SOURCE = 40
MAX_FILE = 256 * 1024         # past this the writer starts a fresh file; readers notice the truncation


def feed_path() -> Path:
    return Path(os.environ.get("GOOSE_BUDDY_FEED") or state.home() / "feed.jsonl")


def emit(reason: str, source: str = "", path: Path | None = None) -> bool:
    """Append one event. False (and nothing written) for an unknown reason. Never raises on I/O errors."""
    if reason not in REASONS:
        return False
    path = path or feed_path()
    line = json.dumps({"reason": reason, "source": source[:MAX_SOURCE], "ts": round(time.time(), 3)},
                      separators=(",", ":")) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            if f.tell() > MAX_FILE:
                f.truncate(0)
            f.write(line)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except OSError:
        return False


def parse(line: bytes) -> tuple[str, str] | None:
    """(reason, source) for a valid line, else None. Strict: wrong types, big lines and unknown reasons are dropped."""
    if len(line) > MAX_LINE:
        return None
    try:
        d: Any = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("reason"), str) or d["reason"] not in REASONS:
        return None
    src = d.get("source", "")
    return d["reason"], (src if isinstance(src, str) else "")[:MAX_SOURCE]


class FeedReader:
    """Tail of a feed file. `read()` returns new valid events since the last call."""

    def __init__(self, path: Path | None = None, from_start: bool = False) -> None:
        self.path = path or feed_path()
        self.offset: int | None = 0 if from_start else None
        self.dropped = 0

    def read(self, limit: int = 200) -> list[tuple[str, str]]:
        try:
            size = self.path.stat().st_size
        except OSError:
            if self.offset is None:                  # no file yet: everything that appears later is new
                self.offset = 0
            return []
        if self.offset is None:                      # first look: start from now, never replay
            self.offset = size
            return []
        if size < self.offset:                       # truncated or replaced: start over
            self.offset = 0
        if size == self.offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(min(size - self.offset, 1 << 20))
        except OSError:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:                                  # a partial line: wait for the writer to finish it
            if len(chunk) > MAX_LINE:                # ...unless it can never be valid
                self.offset += len(chunk)
                self.dropped += 1
            return []
        self.offset += end + 1
        events: list[tuple[str, str]] = []
        for raw in chunk[:end].split(b"\n"):
            if not raw.strip():
                continue
            ev = parse(raw)
            if ev is None:
                self.dropped += 1
            elif len(events) < limit:
                events.append(ev)
        return events
