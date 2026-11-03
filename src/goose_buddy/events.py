"""Hear a goose session and turn it into reactions.

Goose records every message in ~/.local/share/goose/sessions/sessions.db: assistant tool
requests, tool responses (with an isError flag), user text, and system notifications. The
Watcher polls that database read-only and classifies *locally*; only a reason code is ever
used beyond this module. It costs the main agent nothing and needs no cooperation from it.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable

from . import memory, progress, state, suggestions
from .brain import Brain
from .engine import Companion
from .reactions import (BAD, classify_output, get_name_reaction, get_reaction, is_name_mentioned,
                        streak_reaction, time_reason)

COUNTER_FOR = {"error": "errors_seen", "test-fail": "tests_failed", "large-diff": "large_diffs",
               "commit": "commits_made", "push": "pushes_made", "merge-conflict": "conflicts_resolved",
               "build-fail": "build_fails", "type-error": "type_errors", "lint-fail": "lint_fails",
               "all-green": "all_green", "deploy": "deploys", "release": "releases", "branch": "branches_created",
               "rebase": "rebases_done", "security-warning": "security_warnings",
               "deprecation": "deprecations_seen"}
HOLIDAYS = {(1, 1), (2, 14), (3, 14), (4, 1), (10, 31), (12, 25), (12, 31)}   # upstream's holiday list
MARATHON = 3 * 3600            # upstream: "three hours in"
RECOVERY_WINDOW = 600
GOOD = ("all-green", "success", "deploy", "release")


def default_db() -> Path:
    return Path.home() / ".local" / "share" / "goose" / "sessions" / "sessions.db"


def _texts(parts: list[dict]) -> str:
    return "\n".join(c.get("text", "") for p in parts for c in
                     (p.get("toolResult", {}).get("value", {}).get("content") or []) if isinstance(c, dict))


class Watcher:
    def __init__(self, companion: Companion, db: Path | None = None, brain: Brain | None = None,
                 clock: Callable[[], float] = time.time, rng: Callable[[], float] = random.random,
                 new_session: bool = False) -> None:
        self.c, self.db, self.brain, self.clock, self.rng = companion, db or default_db(), brain, clock, rng
        self.streak = 0
        self.last_bad: tuple[str, float] | None = None
        self.last_xp_tick = clock()
        self.marathon_counted = False
        self.pending_notes: list[str] = []
        self.pending_suggestion: suggestions.Suggestion | None = None
        self.session_start = clock()
        self.last_activity = clock()
        w = state.kv_get("watch", {})
        # A freshly started pane skips whatever happened while it was closed (no burst of stale
        # reactions, XP and mood); a Watcher kept alive between polls resumes from its position.
        self.last_id: int | None = None if new_session else w.get("last_id")
        if new_session:
            self._session_started()

    def _session_started(self) -> None:
        """Count this watch as a session, with upstream's time-of-day flavours."""
        t = time.localtime(self.clock())
        state.bump("sessions")
        if t.tm_hour >= 22 or t.tm_hour < 5:
            state.bump("late_night_sessions")
        elif t.tm_hour < 7:
            state.bump("early_sessions")
        if t.tm_wday >= 5:
            state.bump("weekend_sessions")
        if (t.tm_mon, t.tm_mday) in HOLIDAYS:
            state.bump("holiday_sessions")
        if t.tm_mon == 10:
            state.bump("spooky_sessions")
        progress.track_active_day(self.clock())

    # -- database ---------------------------------------------------------------------------
    def _rows(self) -> list[sqlite3.Row]:
        if not self.db.exists():
            return []
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            if self.last_id is None:             # first run: start from now, never replay history
                self.last_id = con.execute("select coalesce(max(id), 0) from messages").fetchone()[0]
                state.kv_set("watch", {"last_id": self.last_id})
                return []
            return con.execute("select id, role, content_json from messages where id > ? "
                               "order by id limit 300", (self.last_id,)).fetchall()
        finally:
            con.close()

    def poll(self) -> list[dict]:
        """Process new messages; return the reactions shown (dicts with reaction/reason/source)."""
        shown: list[dict] = []
        rows = self._rows()
        for row in rows:
            self.last_id = row["id"]
            try:
                parts = json.loads(row["content_json"])
            except ValueError:
                continue
            for reason, ctx in self._events(row["role"], parts):
                r = self.react(reason, ctx)
                if r:
                    shown.append(r)
        if rows:
            state.kv_set("watch", {"last_id": self.last_id})
            self.last_activity = self.clock()
            minutes = min(5, int((self.clock() - self.last_xp_tick) // 60))
            if minutes:                          # 2 XP per active minute (idle time earns nothing)
                self.last_xp_tick = self.clock()
                self.pending_notes += progress.award_xp("time_spent", self.c, 2 * minutes)[1]
            if not self.marathon_counted and self.clock() - self.session_start > MARATHON:
                self.marathon_counted = True
                state.bump("marathon_sessions")
        else:
            idle = self._idle()
            if idle:
                shown.append(idle)
        return shown

    # -- classification ---------------------------------------------------------------------
    def _events(self, role: str, parts: list[dict]):
        out: list[tuple[str, dict]] = []
        for p in parts:
            t = p.get("type")
            if t == "toolResponse":
                value = p.get("toolResult", {}).get("value", {})
                text = _texts([p])
                hit = classify_output(text)
                if hit:
                    out.append(hit)
                elif value.get("isError"):
                    out.append(("error", {}))
                state.bump("commands_run")
                self._learn(text, hit[0] if hit else ("error" if value.get("isError") else None))
            elif t == "systemNotification" and p.get("notificationType") == "creditsExhausted":
                out.append(("credits", {}))
            elif t == "text" and role == "user":
                memory.consolidate(p.get("text", ""))
                if is_name_mentioned(p.get("text", ""), self.c.name):
                    out.append(("name", {}))
            elif t == "text" and role == "assistant":
                state.bump("turns")
                suggestions.record_turn()
                self.pending_notes += progress.record(self.c, "turn", self.clock())
        # A streak counts consecutive bad results; anything else resets it.
        return out

    def _derived(self, reason: str) -> None:
        """Counters upstream derives from combinations of event, clock and session length."""
        now = self.clock()
        t = time.localtime(now)
        marathon = now - self.session_start > MARATHON
        if reason == "commit" and t.tm_hour < 5:
            state.bump("late_night_commits")
        if reason == "push" and t.tm_wday == 4:
            state.bump("friday_pushes")
        if reason in ("error", "type-error", "build-fail", "lint-fail") and marathon:
            state.bump("marathon_errors")
        if reason == "error" and (t.tm_mon, t.tm_mday) == (4, 1):
            state.bump("april_fools_errors")
        if reason == "merge-conflict" and t.tm_wday >= 5:
            state.bump("weekend_conflicts")
        if self.streak > state.counters().get("max_error_streak", 0):
            with state.locked():
                ev = state.counters()
                ev["max_error_streak"] = self.streak
                state.kv_set("events", ev)
        if reason in ("error", "test-fail", "build-fail", "merge-conflict"):
            self.last_bad = (reason, now)
        elif reason in GOOD and self.last_bad:
            if now - self.last_bad[1] < RECOVERY_WINDOW:
                state.bump("recoveries")
                if marathon:
                    state.bump("marathon_recoveries")
            self.last_bad = None

    def _learn(self, text: str, reason: str | None) -> None:
        """Local-only pattern checks over a tool result: memory, repeated errors, TODOs, test activity."""
        if reason in ("test-fail", "all-green"):
            suggestions.record_test_activity()
        if reason in BAD or reason == "error":
            first = next((l for l in text.splitlines() if re.search(r"error|fail|exception|panic", l, re.I)), "")
            self.pending_suggestion = suggestions.check_repeated_error(first) or self.pending_suggestion
            memory.consolidate(text)
        if reason == "large-diff":
            m = re.search(r"([0-9]+) insertions", text, re.I)
            if m:
                self.pending_suggestion = suggestions.check_large_diff(int(m.group(1))) or self.pending_suggestion
        if re.search(r"^\+.*(TODO|FIXME)", text, re.M):
            self.pending_suggestion = suggestions.check_todo(text) or self.pending_suggestion
        self.pending_suggestion = suggestions.check_no_tests() or self.pending_suggestion

    # -- reacting ---------------------------------------------------------------------------
    def _cooling(self) -> bool:
        last = state.load_reaction()
        return bool(last and self.clock() - last["timestamp"] < state.load_config()["commentCooldown"])

    def react(self, reason: str, ctx: dict[str, str] | None = None, force: bool = False) -> dict | None:
        cfg = state.load_config()
        if reason in COUNTER_FOR:
            state.bump(COUNTER_FOR[reason])
        self.streak = self.streak + 1 if reason in BAD else 0
        self._derived(reason)
        if reason == "pet":
            state.bump("pets")
        notes = self.pending_notes + progress.record(self.c, reason, self.clock())
        self.pending_notes = []
        if notes:                                # level-ups / achievements show beside the bubble
            state.kv_set("announce", {"notes": notes[:3], "timestamp": self.clock()})
        mood = progress.get_mood(self.clock())["current"]
        if cfg["muted"] or (not force and self._cooling()):
            return None
        b = self.c.bones
        if reason == "name":
            text = get_name_reaction(b.species, self.rng)
        elif reason == "credits":
            text = get_reaction("frustrated", b.species, b.rarity, b.stats, rng=self.rng)
        else:
            text = (reason in BAD and self.streak >= 3 and streak_reaction(b.species, self.streak)) \
                or get_reaction(reason, b.species, b.rarity, b.stats, ctx, self.rng)
        if mood in progress.MOOD_REACTIONS and self.rng() < 0.3 and reason not in ("name", "credits"):
            text = f"{progress.MOOD_REACTIONS[mood]} {text}"     # upstream: 30% mood flavour
        shown = state.save_reaction(text, reason, "canned", self.clock())
        state.bump("reactions_given")
        sug = self.pending_suggestion
        self.pending_suggestion = None
        if sug and reason not in ("name", "credits", "pet") and not suggestions.on_cooldown(now=self.clock()):
            suggestions.mark_suggested(self.clock())
            return state.save_reaction(sug.message, "suggestion", "suggestion", self.clock())
        if self.brain:                           # upgrade the canned line if the brain answers in time
            line = self.brain.react(self.c, reason)
            if line:
                shown = state.save_reaction(line, reason, "brain", self.clock())
        return {**shown, "notes": notes}

    def _idle(self) -> dict | None:
        flavour = time_reason(self.clock(), self.rng)
        if flavour and self.clock() - self.last_activity > 120:
            self.last_activity = self.clock()
            return self.react(flavour)
        return None
