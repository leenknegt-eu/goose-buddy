import pytest

from goose_buddy.data.art_data import SPECIES_ART
from goose_buddy.engine import ALL_SPECIES, HATS, Companion, generate_bones
from goose_buddy.render import (FRAME_SEQUENCE, animated_frame, art_frame, compact_frame, display_width,
                                render_widget, strip_ansi, wrap_reaction)


def companion(species="goose", hat="none", shiny=False):
    b = generate_bones("render-test")
    b.species, b.hat, b.shiny = species, hat, shiny
    return Companion(b, "Honk", "p", 0, "x")


def test_every_species_has_three_five_row_frames():
    assert set(SPECIES_ART) == set(ALL_SPECIES)
    assert all(len(f) == 3 and all(len(r) == 5 for r in f) for f in SPECIES_ART.values())


@pytest.mark.parametrize("width", [20, 30, 40, 60, 78, 120])
@pytest.mark.parametrize("species", ["goose", "wyvern", "axolotl", "mushroom"])
def test_widget_never_exceeds_the_terminal_width(width, species):
    for reaction in (None, "HONK. *necks aggressively* and a very long reaction " * 4):
        for line in render_widget(companion(species, "crown", True), reaction, ["First Steps"], width, tick=3):
            assert display_width(line) <= width, (width, species, repr(strip_ansi(line)))


def test_bubble_is_dropped_as_a_unit_when_it_cannot_fit():
    narrow = [strip_ansi(l) for l in render_widget(companion(), "hello there friend", [], 24)]
    assert not any(".-" in l or "hello" in l or "-- " in l for l in narrow) and any("Honk" in l for l in narrow)
    wide = [strip_ansi(l) for l in render_widget(companion(), "hello there friend", [], 70)]
    assert any("hello there friend" in l for l in wide) and any("-- " in l for l in wide)


def test_hats_overlay_the_top_row_and_the_wyvern_has_its_own():
    assert art_frame("goose", "°", 0, "tophat")[0].strip() == "[___]"
    assert art_frame("wyvern", "°", 0, "tophat")[0] == "} [___] {"
    assert art_frame("goose", "°", 0, "none")[0].strip() == ""


def test_animation_cycle_blinks_and_returns_to_rest():
    frames = [animated_frame("goose", "×", "none", t) for t in range(len(FRAME_SEQUENCE))]
    assert any("-" in f[1] for f in frames) and frames[0] == art_frame("goose", "×", 0)
    assert len({tuple(f) for f in frames}) >= 3 and compact_frame(frames[0]) == [r for r in frames[0] if r.strip()][:3]


def test_wrap_respects_width_and_line_cap():
    lines = wrap_reaction("a b c " * 80, 20, 4)
    assert len(lines) == 4 and all(display_width(l) <= 20 for l in lines) and lines[-1].endswith("…")
