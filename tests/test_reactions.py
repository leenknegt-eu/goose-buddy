import itertools

from goose_buddy import reactions as R
from goose_buddy.data import reaction_data as D


def seq(*vals):
    it = itertools.cycle(vals)
    return lambda: next(it)


def test_classifier_on_realistic_output():
    cases = {
        "[main 3fa9c12] fix stuff\n 3 files changed, 12 insertions(+)": "commit",
        "FAILED tests/test_a.py::t - AssertionError\n2 failed, 5 passed": "test-fail",
        "Traceback (most recent call last):\n  File x\nValueError: nope": "error",
        "CONFLICT (content): Merge conflict in a.c": "merge-conflict",
        "All 42 tests passed": "all-green", "Build failed with 3 errors": "build-fail",
        "src/a.ts(3,4): error TS2322: Type 'string' is not assignable": "type-error",
    }
    for text, reason in cases.items():
        assert R.classify_output(text)[0] == reason, text
    assert R.classify_output("hello world, nothing to see") is None


def test_a_python_traceback_is_an_error_not_a_lint_failure():
    # upstream's lint matcher matches the "Error:" in "ValueError:"; we deliberately check tracebacks first
    assert R.classify_output("Traceback (most recent call last):\n  File x\nKeyError: 'a'")[0] == "error"
    assert R.classify_output("ruff: 3 problems (3 errors)")[0] == "lint-fail"


def test_large_diff_needs_more_than_80_lines():
    small = R.classify_output("\n+ 5 insertions")
    assert small is None or small[0] != "large-diff"
    # the commit matcher fires first on real `git` output; the 80-line rule applies to bare diffstats
    assert R.classify_output("\n+ 200 insertions(+)")[0] == "large-diff"


def test_every_reason_the_brain_may_name_has_a_canned_fallback():
    for reason in R.REASON_TEXT:
        if reason in ("name",):
            continue
        assert D.REACTIONS.get(reason), f"no canned pool for {reason}"


def test_get_reaction_is_deterministic_with_an_injected_rng():
    a = R.get_reaction("test-fail", "goose", "common", {"SNARK": 10}, rng=seq(0.0))
    assert a == R.get_reaction("test-fail", "goose", "common", {"SNARK": 10}, rng=seq(0.0)) and a != "..."


def test_context_placeholders_are_filled():
    out = R.get_reaction("large-diff", "goose", "common", {"CHAOS": 99}, {"lines": "321"}, rng=seq(0.0))
    assert "{lines}" not in out and "{count}" not in out


def test_stat_overrides_and_flair_only_apply_when_earned():
    snark = {"SNARK": 95, "CHAOS": 1, "PATIENCE": 1, "DEBUGGING": 1, "WISDOM": 1}
    assert R.get_reaction("error", "goose", "common", snark, rng=seq(0.1)) in D.SNARK_OVERRIDES["error"]
    plain = R.get_reaction("error", "goose", "common", {k: 10 for k in snark}, rng=seq(0.9))
    assert not any(f in plain for f in D.RARITY_FLAIR["legendary"]["pool"])


def test_name_mention_is_word_bounded_and_case_insensitive():
    assert R.is_name_mentioned("hey Honk, look", "honk") and not R.is_name_mentioned("honkers", "honk")
    assert not R.is_name_mentioned("anything", "")


def test_streaks_use_the_goose_lines_and_the_rest_a_generic_one():
    assert R.streak_reaction("goose", 3).startswith("HONK") and "three" in R.streak_reaction("rabbit", 3)
    assert "left the room" in R.streak_reaction("cat", 3) and "ROOT CAUSE".lower() in R.streak_reaction("owl", 5).lower()
    assert R.streak_reaction("goose", 4) is None and R.streak_reaction("goose", 25).startswith("HONKING HAS CEASED")


def test_recovery_combo_and_holiday_lines():
    import time
    assert "VICTORY" in R.recovery_reaction("goose", "error") and "FIXED" in R.recovery_reaction("pig", "error")
    assert R.recovery_reaction("pig", "stash") is None
    fri_3am = time.mktime((2026, 11, 6, 3, 0, 0, 0, 0, -1))
    assert R.pick_combo("error", fri_3am, 0, lambda: 0.0) == "late-night-error"
    assert R.pick_combo("push", fri_3am, 0, lambda: 0.0) == "friday-push"
    assert R.pick_combo("error", fri_3am, 0, lambda: 0.99) is None
    assert "FRIDAY DEPLOYS" in R.combo_reaction("goose", "friday-push")
    assert "ballad" in R.combo_reaction("pig", "friday-push")
    xmas = time.mktime((2026, 12, 25, 12, 0, 0, 0, 0, -1))
    assert R.holiday_reaction("goose", xmas, lambda: 0.0) == ("christmas", "FESTIVE HONK! HONK HONK HO-HO-HONK!")
    assert R.holiday_reaction("pig", xmas, lambda: 0.0)[1].startswith("*wears tiny santa")
    assert R.holiday_reaction("goose", xmas, lambda: 0.9) is None
    assert R.holiday_reaction("goose", time.mktime((2026, 10, 5, 12, 0, 0, 0, 0, -1)), lambda: 0.0)[0] == "spooky-season"
