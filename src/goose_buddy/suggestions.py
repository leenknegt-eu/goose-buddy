"""Proactive nudges (port of coding-buddy server/suggestions.ts, MIT; see NOTICE).

Pure local pattern checks over the *shape* of what happened (counts, a hash of an error line).
Text is hashed or pattern-matched here and discarded; nothing is stored or sent."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import state


@dataclass(frozen=True)
class Suggestion:
    id: str
    pattern: str
    message: str
    severity: str  # info | warn | praise


TEMPLATES: dict[str, list[Suggestion]] = {
    "repeated_error": [
        Suggestion("rep_err_1", "repeated_error", "That error keeps showing up. Time to fix it properly?", "warn"),
        Suggestion("rep_err_2", "repeated_error", "*counting on tentacles* that error is now a roommate. Time to evict it.", "warn")],
    "escalated_large_diff": [
        Suggestion("lrg_diff_1", "escalated_large_diff", "*nervous laughter* that's a big one. Maybe split it up?", "info"),
        Suggestion("lrg_diff_2", "escalated_large_diff", "That's not a diff, that's a whole new file. Consider a branch.", "warn")],
    "no_tests_long_session": [
        Suggestion("no_test_session_1", "no_tests_long_session", "You've been busy. But where are the tests?", "info")],
    "todo_comment": [
        Suggestion("todo_1", "todo_comment", "TODO noted. Don't forget to actually do it.", "info"),
        Suggestion("fixme_1", "todo_comment", "FIXME seen. The clock is ticking.", "warn")],
}


def simple_hash(s: str) -> str:
    """Upstream's 32-bit string hash (h = h*31 + c), hex of its absolute value."""
    h = 0
    for ch in s:
        h = (((h << 5) - h) + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return format(abs(h), "x")


def _history() -> dict:
    h = {"recentErrors": [], "recentDiffs": [], "turnsSinceTest": 0, "turnCount": 0}
    h.update(state.kv_get("suggestions", {}))
    return h


def _save(h: dict) -> None:
    state.kv_set("suggestions", h)


def check_repeated_error(error_text: str) -> Suggestion | None:
    with state.locked():
        h = _history()
        sig = simple_hash(error_text[:200])
        h["recentErrors"] = ([sig] + h["recentErrors"])[:20]
        _save(h)
    n = h["recentErrors"].count(sig)
    return TEMPLATES["repeated_error"][1 if n >= 5 else 0] if n >= 3 else None


def check_large_diff(lines: int) -> Suggestion | None:
    with state.locked():
        h = _history()
        h["recentDiffs"] = ([lines] + h["recentDiffs"])[:20]
        h["turnCount"] += 1
        _save(h)
    t = TEMPLATES["escalated_large_diff"]
    return t[1] if lines > 300 else t[0] if lines > 150 else None


def check_todo(text: str) -> Suggestion | None:
    t = TEMPLATES["todo_comment"]
    return t[1] if re.search("FIXME", text, re.I) else t[0] if re.search("TODO", text, re.I) else None


def record_turn() -> None:
    with state.locked():
        h = _history()
        h["turnCount"] += 1
        h["turnsSinceTest"] += 1
        _save(h)


def record_test_activity() -> None:
    with state.locked():
        h = _history()
        h["turnsSinceTest"] = 0
        _save(h)


def check_no_tests() -> Suggestion | None:
    return TEMPLATES["no_tests_long_session"][0] if _history()["turnsSinceTest"] >= 10 else None


def on_cooldown(seconds: float = 180, now: float | None = None) -> bool:
    last = state.kv_get("last_suggestion", None)
    return last is not None and (time.time() if now is None else now) - last < seconds


def mark_suggested(now: float | None = None) -> None:
    state.kv_set("last_suggestion", time.time() if now is None else now)
