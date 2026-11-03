import json
import time

import pytest

from goose_buddy import memory, menagerie, progress, state, suggestions
from goose_buddy.cli import main
from goose_buddy.data.achievement_data import ACHIEVEMENTS
from goose_buddy.identity import ensure_companion


@pytest.fixture
def buddy():
    return ensure_companion()[0]


def test_level_table_matches_upstream():
    assert progress.compute_level(0) == 1 and progress.compute_level(99) == 1
    assert progress.compute_level(100) == 2 and progress.compute_level(44000) == 20
    assert progress.compute_level(10**6) == 20 and progress.xp_to_next(10**6) == 0
    assert progress.xp_to_next(0) == 100


def test_xp_levels_up_and_unlocks_once(buddy):
    _, notes = progress.award_xp("tests_passed", buddy, amount=900)   # straight to level 5
    s = progress.xp_state()
    assert s["level"] == 5 and "celebrate_level5" in s["unlockedReactions"]
    assert "bonus_eye" in s["unlockedUpgrades"]
    assert any("level 5" in n for n in notes)
    _, again = progress.award_xp("turn", buddy)
    assert not any("level" in n for n in again)


def test_species_gated_reaction_for_goose(buddy):
    buddy.bones.species = "goose"
    progress.award_xp("turn", buddy, amount=3300)
    assert "boss_fight_level8" in progress.xp_state()["unlockedReactions"]
    assert any("debug" in t for t in progress.available_reactions("goose", "common"))
    assert not any("cracks knuckles" in t for t in progress.available_reactions("goose", "common"))


def test_stat_boost_and_shiny_persist(buddy):
    before = buddy.bones.stats[buddy.bones.peak]
    progress.award_xp("turn", buddy, amount=10000)                 # level 12: shiny + stat boost
    saved = state.load_companion()
    assert saved.bones.shiny
    assert saved.bones.stats[saved.bones.peak] == min(100, before + 5)
    progress.award_xp("turn", buddy)                               # not applied twice
    assert state.load_companion().bones.stats[saved.bones.peak] == min(100, before + 5)


def test_three_errors_in_five_minutes_is_chaos():
    t = 1_000_000.0
    for i in range(3):
        m = progress.shift_mood("error", t + i)
    assert m["current"] == "chaotic" and m["intensity"] == 2
    assert progress.shift_mood("tests_pass", t + 5)["current"] == "focused"


def test_mood_window_decays():
    t = 1_000_000.0
    progress.shift_mood("error", t)
    progress.shift_mood("error", t + 1)
    m = progress.get_mood(t + 1 + progress.RECENT_WINDOW + 1)
    assert m["recentErrors"] == 0


def test_there_are_131_achievements_and_ids_are_unique():
    assert len(ACHIEVEMENTS) == 131
    assert len({a[0] for a in ACHIEVEMENTS}) == 131
    secret = [a for a in ACHIEVEMENTS if a[5]]
    assert secret and all(a[0] for a in secret)


def test_achievements_unlock_from_counters_and_award_xp(buddy):
    state.bump("errors_seen", 25)
    got = {a[0] for a in progress.check_achievements(buddy)}
    assert {"first_steps", "bug_spotter", "error_whisperer"} <= got
    assert "battle_scarred" not in got
    assert progress.xp_state()["totalXp"] == 50 * len(got)        # 50 each, upstream's rule
    assert progress.check_achievements(buddy) == []               # never awarded twice


def test_secret_achievements_stay_hidden_until_earned(buddy):
    card = progress.achievements_card()
    assert "Battle-Scarred" not in card and "secret achievements" in card
    assert "Battle-Scarred" in progress.achievements_card(show_secret=True)
    state.bump("errors_seen", 100)
    progress.check_achievements(buddy)
    assert "Battle-Scarred" in progress.achievements_card()


def test_meta_achievement_sees_totals_in_one_call(buddy):
    for a in ACHIEVEMENTS:                                         # satisfy every plain counter
        for k, n in a[4]:
            if k != "achievements_unlocked":
                state.bump(k, n) if state.counters().get(k, 0) < n else None
    progress.check_achievements(buddy)
    assert len(progress.unlocked_ids()) == 131                     # incl. the completionist


def test_lock_is_reentrant_and_threadsafe():
    with state.locked():
        with state.locked():
            state.bump("x")
    import threading
    ts = [threading.Thread(target=lambda: [state.bump("y") for _ in range(20)]) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert state.counters()["y"] == 80


# ─── menagerie ───────────────────────────────────────────────────────────────────────────────

def test_save_summon_roundtrip_keeps_renames(buddy):
    assert menagerie.save() == menagerie.slugify(buddy.name)
    first = menagerie.slugify(buddy.name)
    other = menagerie.pick(species="cat", name="Whiskers")
    assert state.load_companion().name == "Whiskers" and other.bones.species == "cat"
    c = state.load_companion()
    c.name = "Whisk"                                               # rename the active buddy
    state.save_companion(c)
    back = menagerie.summon(first)
    assert back.name == buddy.name and state.load_companion().name == buddy.name
    assert menagerie.summon("whiskers").name == "Whisk"            # rename survived the switch
    assert state.counters()["summons"] == 2 and state.counters()["buddies_collected"] == 2


def test_dismiss_rules(buddy):
    menagerie.save()
    menagerie.pick(rarity="rare", name="Bit")
    with pytest.raises(menagerie.MenagerieError, match="active"):
        menagerie.dismiss("bit")
    assert menagerie.dismiss(menagerie.slugify(buddy.name))
    with pytest.raises(menagerie.MenagerieError):
        menagerie.dismiss("nobody")


def test_pick_filters_and_validates(buddy):
    c = menagerie.pick(species="goose", rarity="epic", name="Gus")
    assert (c.bones.species, c.bones.rarity) == ("goose", "epic")
    with pytest.raises(menagerie.MenagerieError, match="unknown species"):
        menagerie.pick(species="dodo")
    with pytest.raises(menagerie.MenagerieError, match="already exists"):
        menagerie.pick(name="Gus")


def test_empty_menagerie_messages(buddy):
    assert "empty" in menagerie.format_listing()
    with pytest.raises(menagerie.MenagerieError, match="empty"):
        menagerie.summon()


# ─── suggestions and memory ──────────────────────────────────────────────────────────────────

def test_simple_hash_matches_upstream_algorithm():
    assert suggestions.simple_hash("") == "0"
    assert suggestions.simple_hash("a") == format(97, "x")
    assert suggestions.simple_hash("abc") == format(96354, "x")    # 97*31*31+98*31+99


def test_repeated_error_escalates():
    s = [suggestions.check_repeated_error("boom") for _ in range(5)]
    assert s[0] is None and s[1] is None
    assert s[2].id == "rep_err_1" and s[4].id == "rep_err_2"


def test_large_diff_and_todo_thresholds():
    assert suggestions.check_large_diff(100) is None
    assert suggestions.check_large_diff(151).id == "lrg_diff_1"
    assert suggestions.check_large_diff(301).id == "lrg_diff_2"
    assert suggestions.check_todo("# fixme later").id == "fixme_1"
    assert suggestions.check_todo("// TODO x").id == "todo_1"
    assert suggestions.check_todo("clean") is None


def test_no_tests_nudge_and_cooldown():
    for _ in range(10):
        suggestions.record_turn()
    assert suggestions.check_no_tests() is not None
    suggestions.record_test_activity()
    assert suggestions.check_no_tests() is None
    assert not suggestions.on_cooldown(now=100)
    suggestions.mark_suggested(100)
    assert suggestions.on_cooldown(180, now=200) and not suggestions.on_cooldown(180, now=300)


def test_memory_learns_projects_and_recurring_bugs():
    text = "see /work/f3decomp/src/main.py and Cargo.toml\nTypeError: x is not a function"
    memory.consolidate(text)
    memory.consolidate(text)
    p = memory.projects()["f3decomp"]
    assert "python" in p["language"] and p["framework"] == "Rust"
    (bug,) = [b for b in memory.bugs().values() if "TypeError" in b["summary"]][:1]
    assert bug["occurrenceCount"] >= 2
    assert memory.resolve_bug(bug["id"])["resolved"] and state.counters()["bugs_resolved"] == 1


def test_memory_can_be_disabled_and_caps_summaries():
    state.save_config(memory={"enabled": False})
    memory.consolidate("/a/src/x.py Error: boom")
    assert memory.projects() == {}
    state.save_config(memory={"enabled": True})
    memory.consolidate("/a/src/x.py Error: " + "z" * 5000)
    assert all(len(b["summary"]) <= 200 for b in memory.bugs().values())


def test_preference_confidence_grows():
    memory.set_preference("indent", "2", "ts")
    p = memory.set_preference("indent", "2", "ts")
    assert 0.19 < p["confidence"] < 0.21


# ─── CLI ─────────────────────────────────────────────────────────────────────────────────────

def test_cli_new_commands(capsys):
    for cmd in (["xp"], ["mood"], ["achievements"], ["list"], ["save"], ["list"], ["memory"]):
        assert main(cmd) == 0
    out = capsys.readouterr().out
    assert "Level 1" in out and "First Steps" in out and "[" in out
    assert main(["dismiss", "nobody"]) == 1


def test_cli_unlocks_are_announced(capsys):
    main(["pet"])
    assert "Achievement unlocked: First Steps" in capsys.readouterr().out
