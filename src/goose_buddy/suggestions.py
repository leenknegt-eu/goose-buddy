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
    "new_file_no_test": [
        Suggestion("no_test_1", "new_file_no_test", "New file, no test? I see a bug in your future.", "info")],
    "sequence_rename": [
        Suggestion("rename_1", "sequence_rename", "Renaming things is good, but update all the references too.", "warn")],
    "long_function": [
        Suggestion("long_fn_1", "long_function", "That's one long function. Consider breaking it up.", "warn"),
        Suggestion("long_fn_2", "long_function", "*squints* is that function or a novel? Break it down.", "warn")],
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


SOURCE_EXT = {"py", "ts", "tsx", "js", "jsx", "rs", "go", "java", "rb", "php", "c", "cpp", "cs", "swift", "kt", "scala", "lua"}
TESTISH = re.compile(r"(^|[/_.-])(tests?|spec|specs)([/_.-]|$)|_test\.|\.test\.|\.spec\.", re.I)
FUNC_HEAD = re.compile(r"^(?P<ind>[+\s]*?)(?:async\s+)?(?:def|function|fn|func)\s+\w+", re.M)


def check_new_file_no_test(text: str) -> Suggestion | None:
    """Files created in this output (git's `create mode` / `new file:`), none of them a test."""
    made = re.findall(r"create mode \d+ (\S+)|new file:\s+(\S+)", text)
    paths = [a or b for a, b in made]
    src = [p for p in paths if p.rsplit(".", 1)[-1] in SOURCE_EXT and not TESTISH.search(p)]
    if src and not any(TESTISH.search(p) for p in paths):
        return TEMPLATES["new_file_no_test"][0]
    return None


def check_renames(text: str) -> Suggestion | None:
    """Three or more renames in a row (counted across outputs, reset when it fires)."""
    n = len(re.findall(r"^\s*rename (?:from|to) |^\s*renamed:|^R\d*\s+\S+\s+\S+", text, re.M))
    if not n:
        return None
    with state.locked():
        h = _history()
        h["recentRenames"] = h.get("recentRenames", 0) + n
        fire = h["recentRenames"] >= 3
        if fire:
            h["recentRenames"] = 0
        _save(h)
    return TEMPLATES["sequence_rename"][0] if fire else None


def check_long_function(text: str) -> Suggestion | None:
    """A def/function/fn whose indented body runs 50+ (or 100+) lines in this output."""
    lines = text.splitlines()
    best = 0
    for m in FUNC_HEAD.finditer(text):
        start = text.count("\n", 0, m.start())
        indent = len(m.group("ind").replace("+", ""))
        n = 0
        for line in lines[start + 1:]:
            body = line.lstrip("+")
            if body.strip() and len(body) - len(body.lstrip()) <= indent:
                break
            n += 1
        best = max(best, n)
    t = TEMPLATES["long_function"]
    return t[1] if best >= 100 else t[0] if best >= 50 else None
