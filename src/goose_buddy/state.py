"""Persistent state shared by the watcher, the pane, the CLI and the MCP server.

Plain JSON files under $GOOSE_BUDDY_HOME (default ~/.config/goose/buddy). Writes are atomic
(temp file + rename) and read-modify-write sections hold an flock, because several processes
touch the same files. Nothing here ever stores an API key.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .engine import Companion

DEFAULT_CONFIG: dict[str, Any] = {
    "commentCooldown": 30,       # seconds between spontaneous reactions
    "reactionTTL": 25,           # seconds a speech bubble stays up
    "muted": False,
    "theme": "dark",
    "bubbleStyle": "classic",    # "classic" (.--. | |) or "round" ((~~) ( ))
    "moodEnabled": True,         # mood flourishes on reactions
    "suggestionsEnabled": True,  # proactive nudges
    "suggestionCooldown": 180,   # seconds between nudges
    "species_pool": "original",  # "original" (18, Claude Code's) or "coding-buddy" (20)
    "species_override": "",      # e.g. "goose": force the species, keep every other roll
    "user_id": "",               # explicit identity seed; empty = read ~/.claude.json
    "brain": {"enabled": True, "model": "beam-free", "minInterval": 25, "maxPerHour": 40,
              "timeout": 20.0},
}


def home() -> Path:
    p = Path(os.environ.get("GOOSE_BUDDY_HOME") or Path.home() / ".config" / "goose" / "buddy")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


_depth = 0
_tlock = threading.RLock()


@contextmanager
def locked() -> Iterator[None]:
    """Exclusive across threads (RLock) and processes (flock); reentrant, so a locked section
    may call helpers that lock too."""
    global _depth
    with _tlock:
        if _depth:
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        with open(home() / ".lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            _depth = 1
            try:
                yield
            finally:
                _depth = 0
                fcntl.flock(f, fcntl.LOCK_UN)


# ─── config ──────────────────────────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    saved = _read(home() / "config.json", {})
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_config(**changes: Any) -> dict[str, Any]:
    with locked():
        saved = _read(home() / "config.json", {})
        saved.update(changes)
        _write(home() / "config.json", saved)
    return load_config()


# ─── companion ───────────────────────────────────────────────────────────────────────────────

def load_companion() -> Companion | None:
    d = _read(home() / "companion.json", None)
    return Companion.from_dict(d) if d else None


def save_companion(c: Companion) -> None:
    with locked():
        _write(home() / "companion.json", c.to_dict())


# ─── reaction (what the bubble says right now) ───────────────────────────────────────────────

def load_reaction() -> dict[str, Any] | None:
    return _read(home() / "reaction.json", None)


def save_reaction(text: str, reason: str, source: str, now: float | None = None) -> dict[str, Any]:
    r = {"reaction": text, "reason": reason, "source": source, "timestamp": time.time() if now is None else now}
    with locked():
        _write(home() / "reaction.json", r)
    return r


def live_reaction(ttl: float | None = None, now: float | None = None) -> dict[str, Any] | None:
    r = load_reaction()
    if not r:
        return None
    ttl = load_config()["reactionTTL"] if ttl is None else ttl
    return r if (time.time() if now is None else now) - r["timestamp"] <= ttl else None


# ─── counters (feed XP and achievements later) ───────────────────────────────────────────────

def bump(key: str, amount: int = 1) -> int:
    with locked():
        events = _read(home() / "events.json", {})
        events[key] = int(events.get(key, 0)) + amount
        _write(home() / "events.json", events)
        return events[key]


def counters() -> dict[str, int]:
    return _read(home() / "events.json", {})


def kv_get(name: str, default: Any = None) -> Any:
    return _read(home() / f"{name}.json", default)


def kv_set(name: str, value: Any) -> None:
    with locked():
        _write(home() / f"{name}.json", value)
