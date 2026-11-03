from goose_buddy import state
from goose_buddy.engine import (ALL_SPECIES, ORIGINAL_SPECIES, Companion, generate_bones, hash_string,
                                generate_personality, mulberry32, render_card, render_face)
from goose_buddy.identity import ensure_companion, hatch

# Golden vectors copied from coding-buddy's own core/engine.test.ts (generated with its 20-species pool).
GOLDEN = {
    "golden-user-alpha": ("common", "capybara", "·", {"DEBUGGING": 23, "PATIENCE": 22, "CHAOS": 9, "WISDOM": 32, "SNARK": 59}, "SNARK", "CHAOS"),
    "golden-user-beta": ("common", "chonk", "@", {"DEBUGGING": 3, "PATIENCE": 77, "CHAOS": 40, "WISDOM": 33, "SNARK": 5}, "PATIENCE", "DEBUGGING"),
    "legendary-seed-1": ("uncommon", "capybara", "◉", {"DEBUGGING": 11, "PATIENCE": 16, "CHAOS": 20, "WISDOM": 49, "SNARK": 76}, "SNARK", "DEBUGGING"),
}


def test_matches_upstream_golden_vectors():
    for uid, (rarity, species, eye, stats, peak, dump) in GOLDEN.items():
        b = generate_bones(uid, species_pool="coding-buddy")
        assert (b.rarity, b.species, b.eye, b.stats, b.peak, b.dump) == (rarity, species, eye, stats, peak, dump), uid


def test_pool_changes_only_the_species():
    """The upstream regression: 18 vs 20 species moves the species and nothing else."""
    for uid in GOLDEN:
        a, b = generate_bones(uid, species_pool="original"), generate_bones(uid, species_pool="coding-buddy")
        assert a.species in ORIGINAL_SPECIES and b.species in ALL_SPECIES
        assert (a.rarity, a.eye, a.hat, a.shiny, a.stats, a.peak, a.dump) == (b.rarity, b.eye, b.hat, b.shiny, b.stats, b.peak, b.dump)
    assert generate_bones("golden-user-alpha").species != "capybara"       # the original pool differs


def test_primitives_are_deterministic_and_in_range():
    assert hash_string("friend") == hash_string("friend") and 0 <= hash_string("x" * 100) < 2 ** 32
    r1, r2 = mulberry32(42), mulberry32(42)
    seq = [r1() for _ in range(50)]
    assert seq == [r2() for _ in range(50)] and all(0 <= x < 1 for x in seq)


def test_invariants_over_many_seeds():
    for i in range(300):
        b = generate_bones(f"user-{i}")
        assert b.peak != b.dump and all(1 <= v <= 100 for v in b.stats.values())
        assert b.rarity != "common" or b.hat == "none"


def test_faces_and_card():
    assert render_face("turtle", "·") == "[·_·]" and render_face("duck", "normal") == "(°>"
    card = render_card(generate_bones("golden-user-alpha"))
    assert len(card) == 7 and any("▲" in l for l in card) and any("▼" in l for l in card)


def test_personality_is_stable_and_mentions_species():
    b = generate_bones("abcdef12-0000")
    assert generate_personality(b, "abcdef12-0000") == generate_personality(b, "abcdef12-0000")
    assert b.species in generate_personality(b, "abcdef12-0000")
    generate_personality(b, "anon")        # a non-hex id must not crash


def test_species_override_keeps_every_other_roll(monkeypatch):
    base = hatch(user_id="test-seed")
    state.save_config(species_override="goose")
    forced = hatch(user_id="test-seed")
    assert forced.bones.species == "goose"
    assert (forced.bones.rarity, forced.bones.eye, forced.bones.hat, forced.bones.stats) == \
           (base.bones.rarity, base.bones.eye, base.bones.hat, base.bones.stats)


def test_ensure_companion_persists_and_round_trips(monkeypatch):
    monkeypatch.setenv("GOOSE_BUDDY_USER_ID", "someone-123456")
    c, created = ensure_companion()
    c2, created2 = ensure_companion()
    assert created and not created2 and c.to_dict() == c2.to_dict()
    assert Companion.from_dict(c.to_dict()).to_dict() == c.to_dict()
