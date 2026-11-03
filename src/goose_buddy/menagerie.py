"""The menagerie: save, summon, list, dismiss and pick buddies (port of upstream's slot manifest).

`companion.json` stays the active buddy (everything else reads it). `menagerie.json` holds the saved
slots; switching writes the active buddy back to its slot first, so renames are never lost.
XP, mood and achievements are shared across buddies, as upstream does.
"""

from __future__ import annotations

import os
import random
import re
import time

from . import state
from .data.reaction_data import FALLBACK_NAMES
from .engine import ALL_SPECIES, RARITIES, RARITY_STARS, Companion, generate_bones, generate_personality
from .identity import ensure_companion


class MenagerieError(Exception):
    pass


def slugify(name: str) -> str:
    s = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", name.lower())).strip("-")
    return s[:14] or "buddy"


def _load() -> dict:
    m = state.kv_get("menagerie", None) or {}
    return {"active": m.get("active", ""), "companions": m.get("companions", {})}


def _flush_active(m: dict) -> None:
    """Write the live companion back into its slot (if it has one)."""
    c = state.load_companion()
    if c and m["active"] in m["companions"]:
        m["companions"][m["active"]] = c.to_dict()


def listing() -> list[tuple[str, Companion, bool]]:
    m = _load()
    _flush_active(m)
    return [(slot, Companion.from_dict(d), slot == m["active"]) for slot, d in m["companions"].items()]


def save(slot: str | None = None) -> str:
    """Save the active buddy to a slot (default: its slugified name) and make that slot active."""
    c, _ = ensure_companion()
    with state.locked():
        m = _load()
        _flush_active(m)
        target = slugify(slot or c.name)
        if target in m["companions"] and target != m["active"]:
            raise MenagerieError(f'slot "{target}" already exists; choose a different name')
        m["companions"][target], m["active"] = c.to_dict(), target
        state.kv_set("menagerie", m)
    state.bump("buddies_collected")
    state.bump("saves")
    return target


def summon(slot: str | None = None) -> Companion:
    with state.locked():
        m = _load()
        _flush_active(m)
        if not m["companions"]:
            raise MenagerieError("your menagerie is empty: save a buddy first")
        target = slugify(slot) if slot else random.choice(list(m["companions"]))
        if target not in m["companions"]:
            raise MenagerieError(f'no buddy in slot "{target}"; see `goose-buddy list`')
        c = Companion.from_dict(m["companions"][target])
        m["active"] = target
        state.kv_set("menagerie", m)
        state.save_companion(c)
    state.bump("summons")
    return c


def dismiss(slot: str) -> str:
    with state.locked():
        m = _load()
        target = slugify(slot)
        if target not in m["companions"]:
            raise MenagerieError(f'no buddy in slot "{target}"')
        if target == m["active"]:
            raise MenagerieError(f'cannot dismiss the active buddy; summon another first, then dismiss "{target}"')
        del m["companions"][target]
        state.kv_set("menagerie", m)
    state.bump("dismissals")
    return target


def pick(species: str | None = None, rarity: str | None = None, name: str | None = None,
         rng: random.Random | None = None) -> Companion:
    """Roll fresh buddies until one matches; it joins the menagerie and becomes active."""
    if species and species not in ALL_SPECIES:
        raise MenagerieError(f"unknown species {species!r}")
    if rarity and rarity not in RARITIES:
        raise MenagerieError(f"unknown rarity {rarity!r}")
    rng = rng or random.Random()
    cfg = state.load_config()
    attempts = {"legendary": 400_000, "epic": 200_000, "rare": 100_000}.get(rarity or "", 50_000)
    for _ in range(attempts):
        uid = "%032x" % rng.getrandbits(128)
        bones = generate_bones(uid, species_pool="coding-buddy" if species in ("wyvern", "pikachu") else cfg["species_pool"])
        if (species and bones.species != species) or (rarity and bones.rarity != rarity):
            continue
        break
    else:
        raise MenagerieError(f"no match after {attempts:,} attempts; try broader criteria")
    with state.locked():
        m = _load()
        _flush_active(m)
        taken = set(m["companions"])
        buddy_name = (name or "").strip()[:14] or next(
            (n for n in rng.sample(FALLBACK_NAMES, len(FALLBACK_NAMES)) if slugify(n) not in taken), f"buddy-{len(taken)}")
        slot = slugify(buddy_name)
        if slot in taken:
            raise MenagerieError(f'a buddy in slot "{slot}" already exists; pick a different name')
        c = Companion(bones, buddy_name, generate_personality(bones, uid), int(time.time() * 1000), uid)
        m["companions"][slot], m["active"] = c.to_dict(), slot
        state.kv_set("menagerie", m)
        state.save_companion(c)
    state.bump("buddies_collected")
    return c


def format_listing() -> str:
    rows = listing()
    if not rows:
        return "Your menagerie is empty. Use `save` to add the current buddy."
    return "\n".join(f"  {c.name} [{slot}] — {c.bones.rarity} {c.bones.species} {RARITY_STARS[c.bones.rarity]}"
                     f"{' ✨' if c.bones.shiny else ''}{'  ← active' if act else ''}" for slot, c, act in rows)
