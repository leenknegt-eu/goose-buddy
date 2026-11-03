from goose_buddy import state
from goose_buddy.cli import main
from goose_buddy.engine import generate_bones, generate_personality
from goose_buddy.identity import ensure_companion


def test_identity_settings_rehatch_an_existing_buddy_and_keep_its_name(monkeypatch, capsys):
    monkeypatch.setenv("GOOSE_BUDDY_USER_ID", "test-seed-for-cli")
    main(["rename", "Honk"])
    before = ensure_companion()[0]
    assert before.bones.species != "goose"
    main(["config", "species_override=goose"])
    after = ensure_companion()[0]
    assert after.bones.species == "goose" and after.name == "Honk"
    assert "goose" in after.personality                     # the text follows the species
    assert (after.bones.rarity, after.bones.eye, after.bones.hat, after.bones.stats) == \
           (before.bones.rarity, before.bones.eye, before.bones.hat, before.bones.stats)


def test_non_identity_settings_do_not_rehatch(monkeypatch):
    monkeypatch.setenv("GOOSE_BUDDY_USER_ID", "abc12345-x")
    main(["rename", "Keep"])
    hatched = ensure_companion()[0].hatched_at
    main(["config", "commentCooldown=5"])
    assert state.load_config()["commentCooldown"] == 5 and ensure_companion()[0].hatched_at == hatched


def test_rename_validates_length(capsys):
    assert main(["rename", "x" * 15]) == 2 and main(["rename", ""]) in (2, 0)


def test_mute_and_unmute_round_trip():
    main(["mute"]); assert state.load_config()["muted"] is True
    main(["unmute"]); assert state.load_config()["muted"] is False


def test_personality_uses_the_right_article():
    b = generate_bones("art-test")
    b.rarity = "uncommon"
    assert generate_personality(b, "abc").startswith("An uncommon")
    b.rarity = "rare"
    assert generate_personality(b, "abc").startswith("A rare")
