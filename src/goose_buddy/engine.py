"""Deterministic companion generation: wyhash -> mulberry32 -> species, rarity, stats.

Ported from coding-buddy's core/engine.ts, which in turn reproduces Claude Code's original
algorithm. Verified against that project's golden test vectors (tests/test_engine.py).

One deliberate difference. coding-buddy appended two species (wyvern, pikachu) to the pool
the generator draws from, and species is chosen as `pool[floor(rng * len(pool))]`, so that
silently changed the species of nearly every existing buddy. Here the pool is a choice:
"original" (the 18 species Claude Code shipped, the default) or "coding-buddy" (all 20).
Every other field is identical under both, because the draw consumes one random number
either way.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from .data.engine_data import DUMP_PHRASES, FACE_TEMPLATES, PEAK_PHRASES, RARITY_CLOSER

SALT = "friend-2026-401"

ORIGINAL_SPECIES = (
    "duck", "goose", "blob", "cat", "dragon", "octopus", "owl", "penguin", "turtle",
    "snail", "ghost", "axolotl", "capybara", "cactus", "robot", "rabbit", "mushroom", "chonk",
)
EXTRA_SPECIES = ("wyvern", "pikachu")
ALL_SPECIES = ORIGINAL_SPECIES + EXTRA_SPECIES
SPECIES_POOLS = {"original": ORIGINAL_SPECIES, "coding-buddy": ALL_SPECIES}

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")
RARITY_WEIGHTS = {"common": 60, "uncommon": 25, "rare": 10, "epic": 4, "legendary": 1}
RARITY_FLOOR = {"common": 5, "uncommon": 15, "rare": 25, "epic": 35, "legendary": 50}
RARITY_STARS = {r: "★" * (i + 1) for i, r in enumerate(RARITIES)}

STAT_NAMES = ("DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK")
EYES = ("·", "✦", "×", "◉", "@", "°")
DEFAULT_EYE = "°"
HATS = ("none", "crown", "tophat", "propeller", "halo", "wizard", "beanie", "tinyduck")
PERSONALITY_MAX_LENGTH = 500


def resolve_eye_glyph(eye: object) -> str:
    """Keep persisted/runtime eye values from leaking descriptor words into art."""
    return eye if eye in EYES else DEFAULT_EYE  # type: ignore[return-value]


@dataclass
class BuddyBones:
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]
    peak: str
    dump: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BuddyBones":
        return cls(d["rarity"], d["species"], d["eye"], d["hat"], bool(d["shiny"]),
                   dict(d["stats"]), d["peak"], d["dump"])


@dataclass
class Companion:
    bones: BuddyBones
    name: str
    personality: str
    hatched_at: int
    user_id: str

    def to_dict(self) -> dict:
        return {"bones": self.bones.to_dict(), "name": self.name, "personality": self.personality,
                "hatchedAt": self.hatched_at, "userId": self.user_id}

    @classmethod
    def from_dict(cls, d: dict) -> "Companion":
        return cls(BuddyBones.from_dict(d["bones"]), d["name"], d.get("personality", ""),
                   int(d.get("hatchedAt", 0)), d.get("userId", ""))


# ─── Hash: wyhash (Zig stdlib v4.2, as used by Bun.hash), pure Python ──────────────────────

_M64 = (1 << 64) - 1
_M32 = 0xFFFFFFFF
_SECRET = (0xA0761D6478BD642F, 0xE7037ED1A0B428DB, 0x8EBC6AF09C88C6E3, 0x589965CC75374CC3)


def _mum(a: int, b: int) -> tuple[int, int]:
    x = (a & _M64) * (b & _M64)
    return x & _M64, (x >> 64) & _M64


def _mix(a: int, b: int) -> int:
    lo, hi = _mum(a, b)
    return (lo ^ hi) & _M64


def _r8(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off + 8], "little")


def _r4(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


def wyhash(data: bytes, seed: int = 0) -> int:
    n = len(data)
    buf = data
    s0 = (seed ^ _mix((seed ^ _SECRET[0]) & _M64, _SECRET[1])) & _M64
    s1 = s2 = s0
    if n <= 16:
        if n >= 4:
            q = (n >> 3) << 2
            a = ((_r4(buf, 0) << 32) | _r4(buf, q)) & _M64
            b = ((_r4(buf, n - 4) << 32) | _r4(buf, n - 4 - q)) & _M64
        elif n > 0:
            a = (buf[0] << 16) | (buf[n >> 1] << 8) | buf[n - 1]
            b = 0
        else:
            a = b = 0
    else:
        i = 0
        if n >= 48:
            while i + 48 < n:
                st = [s0, s1, s2]
                for j in range(3):
                    ra = _r8(buf, i + 16 * j)
                    rb = _r8(buf, i + 16 * j + 8)
                    st[j] = _mix((ra ^ _SECRET[j + 1]) & _M64, (rb ^ st[j]) & _M64)
                    s0, s1, s2 = st
                i += 48
            s0 = (s0 ^ s1 ^ s2) & _M64
        rem = buf[i:]
        ri = 0
        while ri + 16 < len(rem):
            s0 = _mix((_r8(rem, ri) ^ _SECRET[1]) & _M64, (_r8(rem, ri + 8) ^ s0) & _M64)
            ri += 16
        a = _r8(buf, n - 16)
        b = _r8(buf, n - 8)
    a = (a ^ _SECRET[1]) & _M64
    b = (b ^ s0) & _M64
    a, b = _mum(a, b)
    return _mix((a ^ _SECRET[0] ^ n) & _M64, (b ^ _SECRET[1]) & _M64)


def hash_string(s: str) -> int:
    return wyhash(s.encode("utf-8")) & _M32


# ─── PRNG: Mulberry32 ───────────────────────────────────────────────────────────────────────

def mulberry32(seed: int) -> Callable[[], float]:
    a = seed & _M32

    def rng() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & _M32
        t = ((a ^ (a >> 15)) * (1 | a)) & _M32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & _M32)) & _M32) ^ t
        return ((t ^ (t >> 14)) & _M32) / 4294967296

    return rng


# ─── Generation ─────────────────────────────────────────────────────────────────────────────

def _pick(rng: Callable[[], float], arr) -> str:
    return arr[int(rng() * len(arr))]


def _roll_rarity(rng: Callable[[], float]) -> str:
    roll = rng() * sum(RARITY_WEIGHTS.values())
    for r in RARITIES:
        roll -= RARITY_WEIGHTS[r]
        if roll < 0:
            return r
    return "common"


def generate_bones(user_id: str, salt: str = SALT, species_pool: str = "original") -> BuddyBones:
    pool = SPECIES_POOLS[species_pool]
    rng = mulberry32(hash_string(user_id + salt))
    rarity = _roll_rarity(rng)
    species = _pick(rng, pool)
    eye = _pick(rng, EYES)
    hat = "none" if rarity == "common" else _pick(rng, HATS)
    shiny = rng() < 0.01
    peak = _pick(rng, STAT_NAMES)
    dump = _pick(rng, STAT_NAMES)
    while dump == peak:
        dump = _pick(rng, STAT_NAMES)
    floor = RARITY_FLOOR[rarity]
    stats: dict[str, int] = {}
    for name in STAT_NAMES:
        if name == peak:
            stats[name] = min(100, floor + 50 + int(rng() * 30))
        elif name == dump:
            stats[name] = max(1, floor - 10 + int(rng() * 15))
        else:
            stats[name] = floor + int(rng() * 40)
    return BuddyBones(rarity, species, eye, hat, shiny, stats, peak, dump)


def _js_parse_int_hex(s: str) -> int:
    m = re.match(r"[0-9a-fA-F]+", s)
    return int(m.group(0), 16) if m else 0


def generate_personality(bones: BuddyBones, user_id: str) -> str:
    seed = _js_parse_int_hex(user_id[:8])

    def phrase(arr: list[str], salt: int = 0) -> str:
        return arr[(seed + salt) % len(arr)]

    shiny = " Shimmers faintly in dark mode." if bones.shiny else ""
    peak = phrase(PEAK_PHRASES[bones.peak])
    dump = phrase(DUMP_PHRASES[bones.dump], 1)
    closer = phrase(RARITY_CLOSER.get(bones.rarity, ["Gets the job done."]), 2)
    article = "An" if bones.rarity[0] in "aeiou" else "A"      # upstream wrote "A uncommon"
    return f"{article} {bones.rarity} {bones.species} that {peak}.{shiny} {closer} Though it {dump}."


# ─── Faces and text cards ───────────────────────────────────────────────────────────────────

def render_face(species: str, eye: str) -> str:
    return FACE_TEMPLATES[species].replace("{E}", resolve_eye_glyph(eye))


def render_compact(bones: BuddyBones, name: str, reaction: str | None = None) -> str:
    shiny = "✨" if bones.shiny else ""
    msg = f' │ "{reaction}"' if reaction else ""
    return f"{render_face(bones.species, bones.eye)} {name} {shiny}{RARITY_STARS[bones.rarity]}{msg}"


def render_card(bones: BuddyBones) -> list[str]:
    """The stats card: rarity line plus a bar per stat, with the peak and dump marked."""
    lines = [f"{'✨ ' if bones.shiny else ''}{bones.rarity} {bones.species} {RARITY_STARS[bones.rarity]}", ""]
    for stat in STAT_NAMES:
        v = bones.stats[stat]
        bar = "█" * (v // 5) + "░" * (20 - v // 5)
        mark = " ▲" if stat == bones.peak else " ▼" if stat == bones.dump else ""
        lines.append(f"  {stat:<9} {bar} {v:>3}{mark}")
    return lines
