"""XP, levels, upgrades, mood and achievements (port of coding-buddy server/xp.ts, mood.ts,
achievements.ts, MIT; see NOTICE). Everything is local state under $GOOSE_BUDDY_HOME; nothing
here talks to the network.

`record()` is the single entry point: the watcher, the CLI and the MCP server report what
happened ("pet", "error", ...) and get back the announcements (level-ups, achievements) to show.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import state
from .data.achievement_data import ACHIEVEMENTS

# ─── XP ──────────────────────────────────────────────────────────────────────────────────────

XP_RULES = {"errors_spotted": 15, "tests_passed": 20, "tests_failed": 5, "large_diff": 25, "turn": 1,
            "achievement_unlocked": 50, "time_spent": 2, "buddy_pet": 5}
REASON_XP = {"error": "errors_spotted", "type-error": "errors_spotted", "build-fail": "errors_spotted",
             "lint-fail": "errors_spotted", "test-fail": "tests_failed", "all-green": "tests_passed",
             "large-diff": "large_diff", "turn": "turn", "pet": "buddy_pet"}
XP_LEVELS = {1: 0, 2: 100, 3: 250, 4: 500, 5: 900, 6: 1500, 7: 2300, 8: 3300, 9: 4500, 10: 6000, 11: 7800,
             12: 10000, 13: 12500, 14: 15500, 15: 19000, 16: 23000, 17: 27500, 18: 32500, 19: 38000, 20: 44000}
MAX_LEVEL = 20

# (id, level, template, species or None, rarities or None)
UNLOCKABLE_REACTIONS = [
    ("celebrate_level5", 5, "*does a happy dance* level up!", None, None),
    ("boss_fight_level8", 8, "*rolls up sleeves* time to debug.", ("dragon", "goose"), None),
    ("zen_mode_level10", 10, "*closes all eyes* ...peace.", None, ("rare", "epic", "legendary")),
    ("debug_sprint_level12", 12, "*cracks knuckles* let's squash this.", ("cat", "robot"), None),
]
# (id, level, name, description, icon)
UNLOCKABLE_UPGRADES = [
    ("bonus_eye", 3, "Third Eye", "Your buddy gains a bonus eye for extra perception.", "👁"),
    ("shiny_aura", 7, "Shiny Aura", "A permanent shimmer effect around your buddy.", "✨"),
    ("stat_boost", 12, "Training Bonus", "+5 to peak stat.", "⭐"),
    ("extra_hat_slot", 15, "Hat Collection", "Unlocks the tiny-duck hat permanently.", "🎩"),
]


def compute_level(xp: int) -> int:
    return max(l for l, need in XP_LEVELS.items() if xp >= need)


def xp_to_next(xp: int) -> int:
    lvl = compute_level(xp)
    return 0 if lvl >= MAX_LEVEL else XP_LEVELS[lvl + 1] - xp


def xp_state() -> dict[str, Any]:
    s = {"totalXp": 0, "level": 1, "unlockedReactions": [], "unlockedUpgrades": [], "cosmeticFlags": []}
    s.update(state.kv_get("xp", {}))
    return s


def xp_bar(xp: int, width: int = 20) -> str:
    lvl = compute_level(xp)
    if lvl >= MAX_LEVEL:
        return "█" * width + " MAX"
    lo, hi = XP_LEVELS[lvl], XP_LEVELS[lvl + 1]
    filled = round((xp - lo) / (hi - lo) * width)
    return "█" * filled + "░" * (width - filled) + f" Lvl {lvl}"


def available_reactions(species: str, rarity: str) -> list[str]:
    lvl = xp_state()["level"]
    return [t for _, need, t, sp, ra in UNLOCKABLE_REACTIONS
            if need <= lvl and (not sp or species in sp) and (not ra or rarity in ra)]


def award_xp(event: str, companion, amount: int | None = None) -> tuple[dict[str, Any], list[str]]:
    """Add XP; returns (state, announcements). Applies newly unlocked upgrades to the companion once."""
    notes: list[str] = []
    with state.locked():
        s = xp_state()
        prev = s["level"]
        s["totalXp"] += XP_RULES.get(event, 1) if amount is None else amount
        s["level"] = compute_level(s["totalXp"])
        b = companion.bones
        for rid, need, _t, sp, ra in UNLOCKABLE_REACTIONS:
            if s["level"] >= need and rid not in s["unlockedReactions"] and (not sp or b.species in sp) \
                    and (not ra or b.rarity in ra):
                s["unlockedReactions"].append(rid)
        changed = False
        for uid, need, name, _d, icon in UNLOCKABLE_UPGRADES:
            if s["level"] >= need and uid not in s["unlockedUpgrades"]:
                s["unlockedUpgrades"].append(uid)
                notes.append(f"{icon} upgrade unlocked: {name}")
                changed |= _apply_upgrade(companion, uid, s)
        if s["level"] > prev:
            notes.insert(0, f"⭐ {companion.name} reached level {s['level']}!")
        state.kv_set("xp", s)
    if changed:
        state.save_companion(companion)
    return s, notes


def _apply_upgrade(companion, uid: str, s: dict[str, Any]) -> bool:
    b = companion.bones
    if uid == "bonus_eye":
        s["cosmeticFlags"].append("has_third_eye")
    elif uid == "shiny_aura" and not b.shiny:
        b.shiny = True
        return True
    elif uid == "stat_boost":
        b.stats[b.peak] = min(100, b.stats[b.peak] + 5)
        return True
    return False


# ─── mood ────────────────────────────────────────────────────────────────────────────────────

MOODS = ("happy", "focused", "excited", "tired", "melancholy", "chaotic")
MOOD_FACES = {"happy": "😊", "focused": "🤓", "excited": "🥳", "tired": "😴", "melancholy": "😔", "chaotic": "🤯"}
MOOD_REACTIONS = {"happy": "*blooms*", "focused": "*intensifies*", "excited": "*vibrates with energy*",
                  "tired": "*yawns*", "melancholy": "*sighs gently*", "chaotic": "*glitches*"}
TRANSITIONS = {
    "happy": {"tests_pass": "excited", "achievement": "excited", "long_session": "melancholy", "error": "tired"},
    "focused": {"tests_pass": "happy", "error": "tired", "long_session": "tired"},
    "excited": {"tests_pass": "excited", "error": "focused", "idle": "happy"},
    "tired": {"tests_pass": "focused", "error": "chaotic", "rest": "happy", "idle": "melancholy"},
    "melancholy": {"tests_pass": "focused", "achievement": "happy", "error": "chaotic", "rest": "happy"},
    "chaotic": {"tests_pass": "focused", "idle": "tired", "rest": "melancholy", "error": "chaotic"},
}
MOOD_MODIFIERS = {"happy": {"WISDOM": 5}, "focused": {"DEBUGGING": 5, "CHAOS": -3},
                  "chaotic": {"CHAOS": 8, "WISDOM": -5}, "tired": {"PATIENCE": 3, "DEBUGGING": -3},
                  "melancholy": {"WISDOM": 3}, "excited": {"CHAOS": 5, "PATIENCE": -3}}
RECENT_WINDOW = 300.0


def time_of_day_mood(now: float | None = None) -> str:
    h = datetime.fromtimestamp(time.time() if now is None else now).hour
    if h >= 22 or h < 6:
        return "tired"
    return "melancholy" if h < 9 else "focused" if h < 12 else "happy" if h < 17 else "excited" if h < 20 else "tired"


def _mood_load(now: float) -> dict[str, Any]:
    m = state.kv_get("mood", None) or {"current": time_of_day_mood(now), "since": now, "intensity": 1,
                                       "recentErrors": 0, "recentTests": 0, "recentDiffs": 0}
    if now - m["since"] > RECENT_WINDOW:
        m.update(recentErrors=0, recentTests=0, recentDiffs=0, since=now)
    return m


def get_mood(now: float | None = None) -> dict[str, Any]:
    return _mood_load(time.time() if now is None else now)


def shift_mood(trigger: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    with state.locked():
        m = _mood_load(now)
        key = {"error": "recentErrors", "tests_pass": "recentTests", "large_diff": "recentDiffs"}.get(trigger)
        if key:
            m[key] += 1
        if trigger == "error" and m["recentErrors"] >= 3:
            m.update(current="chaotic", intensity=min(3, m["recentErrors"] - 1), since=now)
        else:
            nxt = TRANSITIONS[m["current"]].get(trigger)
            if nxt and nxt != m["current"]:
                m.update(current=nxt, intensity=min(3, m["intensity"] + 1), since=now)
        state.kv_set("mood", m)
    return m


def mood_modifier(mood: str, stat: str) -> int:
    return MOOD_MODIFIERS.get(mood, {}).get(stat, 0)


# ─── achievements ────────────────────────────────────────────────────────────────────────────

def counter_view() -> dict[str, int]:
    c = dict(state.counters())
    c["achievements_unlocked"] = len(unlocked_ids())
    return c


def unlocked() -> list[dict[str, Any]]:
    return state.kv_get("unlocked", [])


def unlocked_ids() -> set[str]:
    return {u["id"] for u in unlocked()}


def track_active_day(now: float | None = None) -> None:
    today = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")
    with state.locked():
        d = state.kv_get("active_days", {"lastDate": "", "totalDays": 0})
        if d["lastDate"] == today:
            return
        d.update(lastDate=today, totalDays=d["totalDays"] + 1)
        state.kv_set("active_days", d)
    with state.locked():
        ev = state.counters()
        ev["days_active"] = d["totalDays"]
        state.kv_set("events", ev)


def check_achievements(companion, now: float | None = None) -> list[tuple]:
    """Award every achievement whose conditions are met; each is worth XP. Returns the new ones."""
    now = time.time() if now is None else now
    fresh: list[tuple] = []
    with state.locked():
        have = unlocked()
        ids = {u["id"] for u in have}
        c = dict(state.counters())
        for _ in range(2):                       # a second pass lets the meta-achievements see new totals
            c["achievements_unlocked"] = len(ids)
            for a in ACHIEVEMENTS:
                if a[0] in ids or not all(c.get(k, 0) >= n for k, n in a[4]):
                    continue
                have.append({"id": a[0], "unlockedAt": now})
                ids.add(a[0])
                fresh.append(a)
        if fresh:
            state.kv_set("unlocked", have)
            c["achievements_unlocked"] = len(ids)
            state.kv_set("events", c)
    notes: list[str] = []
    for a in fresh:
        _, n = award_xp("achievement_unlocked", companion)
        notes += n
    return fresh


# ─── the single entry point ──────────────────────────────────────────────────────────────────

MOOD_TRIGGER = {"error": "error", "type-error": "error", "build-fail": "error", "lint-fail": "error",
                "test-fail": "tests_fail", "all-green": "tests_pass", "large-diff": "large_diff"}


def record(companion, reason: str, now: float | None = None) -> list[str]:
    """Report that `reason` happened. Awards XP, moves the mood, awards achievements.

    Returns human-readable announcements (level-ups, upgrades, achievements), possibly empty."""
    notes: list[str] = []
    xp_event = REASON_XP.get(reason)
    if xp_event:
        _, n = award_xp(xp_event, companion)
        notes += n
    if reason in MOOD_TRIGGER:
        shift_mood(MOOD_TRIGGER[reason], now)
    new = check_achievements(companion, now)
    if new:
        shift_mood("achievement", now)
        notes += [f"{a[3]} Achievement unlocked: {a[1]}" for a in new]
    return notes


# ─── cards ───────────────────────────────────────────────────────────────────────────────────

def xp_card(companion=None) -> str:
    s = xp_state()
    out = [f"⭐ Level {s['level']} — {s['totalXp']:,} XP", f"   {xp_bar(s['totalXp'])}"]
    nxt = xp_to_next(s["totalXp"])
    out.append(f"   {nxt:,} XP to level {s['level'] + 1}" if nxt else "   MAX LEVEL reached!")
    got = [u for u in UNLOCKABLE_UPGRADES if u[0] in s["unlockedUpgrades"]]
    if got:
        out += ["", "Upgrades:"] + [f"  {i} {n} (Lvl {l}): {d}" for _, l, n, d, i in got]
    if companion:
        rx = available_reactions(companion.bones.species, companion.bones.rarity)
        if rx:
            out += ["", "Unlocked reactions:"] + [f'  "{t}"' for t in rx]
    soon = [u for u in UNLOCKABLE_UPGRADES if u[1] > s["level"]][:3]
    if soon:
        out += ["", "Next unlocks:"] + [f"  {i} {n} at Lvl {l}: {d}" for _, l, n, d, i in soon]
    return "\n".join(out)


def mood_card(companion=None) -> str:
    m = get_mood()
    out = [f"{MOOD_FACES[m['current']]} {m['current'].title()} (intensity {m['intensity']})",
           f"   last 5 min: {m['recentErrors']} errors, {m['recentTests']} green runs, {m['recentDiffs']} big diffs"]
    if companion:
        mods = MOOD_MODIFIERS.get(m["current"], {})
        if mods:
            out.append("   stat shifts: " + ", ".join(f"{k} {v:+d}" for k, v in mods.items()))
    return "\n".join(out)


def achievements_card(show_secret: bool = False) -> str:
    ids = unlocked_ids()
    state.bump("achievement_views")
    total = len(ACHIEVEMENTS)
    filled = round(len(ids) / total * 20)
    out = [f"🏆 Achievements — {len(ids)}/{total}", "   " + "█" * filled + "░" * (20 - filled)]
    hidden = 0
    for aid, name, desc, icon, _c, secret in ACHIEVEMENTS:
        done = aid in ids
        if secret and not done and not show_secret:
            hidden += 1
            continue
        out.append(f"{icon} {'✅' if done else '☐'} {name} — {desc}")
    if hidden:
        out.append(f"   …and {hidden} secret achievements")
    return "\n".join(out)
