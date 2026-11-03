"""Reaction selection: canned pools (the original lines) with stat, rarity and escalation flair.

Ported from coding-buddy's core/reactions.ts and server/hooks/react.ts. The pools themselves are
generated data (data/reaction_data.py); this module is the selection logic. Randomness is
injectable so tests are deterministic.
"""

from __future__ import annotations

import random as _random
import re
import time
from typing import Callable

from .data import reaction_data as D

# Tool-output classifier: first match wins (order matters). Everything runs locally; the
# matched *reason* is all that ever leaves this module.
MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (reason, re.compile(pat, flags)) for reason, pat, flags in (
        ("merge-conflict", r"CONFLICT \(|Merge conflict in|both modified", re.I),
        ("commit", r"[0-9]+ files? changed|\[[a-zA-Z_-]+ [a-f0-9]{7,}\]", re.I),
        ("push", r"To .+:|[0-9a-f]+\.\.[0-9a-f]+\s+\w+ -> \w+|Everything up-to-date|remote: Resolving deltas", re.I),
        ("branch", r"Switched to a new branch|Created branch|onto a new branch", re.I),
        ("rebase", r"Successfully rebased|Rebasing|[0-9]+ done", re.I),
        ("stash", r"Saved working directory|Dropped .+ stash|stash@", re.I),
        ("tag", r"tagged|v[0-9]+\.[0-9]+|tag:.*->", re.I),
        ("security-warning", r"vulnerabilit|CVE-[0-9]{4}-[0-9]+|npm audit|found [0-9]+ vulnerabilities", re.I),
        ("build-fail", r"Build failed|Failed to compile|ERROR in |compilation error|Command failed with exit code", re.I),
        ("type-error", r"TS[0-9]{4}:|Type .+ is not assignable|Argument of type|Cannot find name|Property .+ does not exist", re.I),
        # Deviation from upstream: its lint matcher's bare `error:` also matches `ValueError:`, so a
        # Python traceback was reported as a lint failure. Tracebacks are errors; check them first.
        ("error", r"\btraceback \(most recent call last\)", re.I),
        ("lint-fail", r"✖|[0-9]+ problems? \([0-9]+ error|error:|warning:.+ ESLint|Ruff|flake8.*error|pylint.*error", re.I),
        ("deprecation", r"deprecat|will be removed in|is deprecated|DEPRECATED", re.I),
        ("all-green", r"all [0-9]+ tests passed|0 failures|100% passed|all [0-9]+ passed", re.I),
        ("deploy", r"deployed to|Deployment complete|Published to|vercel.*ready|netlify.*deployed", re.I),
        ("release", r"npm publish|gh release create|Published.*to.*registry", re.I),
        ("coverage", r"Coverage:.*[0-9]+%|All files.*\|.*[0-9]+%", re.I),
        ("test-fail", r"\b[1-9][0-9]* (failed|failing)\b|tests? failed|^FAIL(ED)?|✗|✘", re.I | re.M),
        ("error", r"\berror:|\bexception\b|\btraceback\b|\bpanicked at\b|\bfatal:|exit code [1-9]", re.I | re.M),
        ("large-diff", r"^\+.*[0-9]+ insertions|[0-9]+ files? changed", re.I | re.M),
        ("success", r"\b(all )?[0-9]+ tests? (passed|ok)\b|✓|✔|PASS(ED)?|\bDone\b|\bSuccess\b|exit code 0|Build succeeded", re.I | re.M),
    )
)


def classify_output(text: str) -> tuple[str, dict[str, str]] | None:
    """(reason, context) for a tool result, or None. Mirrors upstream's classifyToolResponse."""
    for reason, pat in MATCHERS:
        if not pat.search(text):
            continue
        ctx: dict[str, str] = {}
        if reason == "commit":
            m = re.search(r"([0-9]+) files? changed", text, re.I)
            if m:
                ctx["files"] = m.group(1)
        elif reason == "branch":
            m = re.search(r"'([^']+)'", text)
            if m:
                ctx["branch"] = m.group(1)
        elif reason == "large-diff":
            m = re.search(r"([0-9]+) insertions", text, re.I)
            if not (m and int(m.group(1)) > 80):
                continue
            ctx["lines"] = m.group(1)
        return reason, ctx
    return None


# Short English descriptions of each reason: the ONLY event content the brain model ever sees.
REASON_TEXT = {
    "error": "a command just failed with an error", "test-fail": "the tests just failed",
    "build-fail": "the build just failed", "type-error": "the type checker found errors",
    "lint-fail": "the linter complained", "merge-conflict": "a merge conflict appeared",
    "commit": "the user committed their changes", "push": "the user pushed to the remote",
    "branch": "a new branch was created", "rebase": "a rebase finished", "stash": "changes were stashed",
    "tag": "a tag was created", "deploy": "a deploy finished", "release": "a release was published",
    "all-green": "all tests are passing", "success": "something succeeded", "large-diff": "a very large change was made",
    "security-warning": "a security warning appeared", "deprecation": "a deprecation warning appeared",
    "coverage": "a coverage report printed", "turn": "the agent just finished a turn",
    "idle": "nothing has happened for a while", "pet": "the user is petting you", "hatch": "you just hatched",
    "late-night": "it is the middle of the night", "early-morning": "it is very early in the morning",
    "friday": "it is Friday", "weekend": "it is the weekend", "monday": "it is Monday",
    "long-session": "the session has run for over an hour", "marathon": "the session has run for hours",
}


def _pick(pool: list[str], rng: Callable[[], float]) -> str:
    return pool[int(rng() * len(pool))]


def _stat_override(reaction: str, reason: str, stats: dict[str, int], rng: Callable[[], float]) -> str:
    roll = rng()
    for stat, thresh, chance, table in (
        ("SNARK", 70, 0.3, D.SNARK_OVERRIDES), ("CHAOS", 70, 0.2, D.CHAOS_OVERRIDES),
        ("PATIENCE", 70, 0.25, D.PATIENCE_OVERRIDES), ("DEBUGGING", 70, 0.25, D.DEBUGGING_OVERRIDES),
        ("WISDOM", 70, 0.2, D.WISDOM_OVERRIDES),
    ):
        if stats.get(stat, 0) >= thresh and roll < chance and table.get(reason):
            return _pick(table[reason], rng)
    return reaction


def _flair(reaction: str, rarity: str, rng: Callable[[], float]) -> str:
    entry = D.RARITY_FLAIR.get(rarity)
    if not entry or rng() >= entry["chance"]:
        return reaction
    flair = _pick(entry["pool"], rng)
    if rarity == "legendary" and rng() < 0.3:
        return f"{flair} {reaction}"
    return f"{reaction} {flair}"


def get_reaction(reason: str, species: str, rarity: str, stats: dict[str, int] | None = None,
                 context: dict[str, str] | None = None, rng: Callable[[], float] = _random.random) -> str:
    general = D.REACTIONS.get(reason)
    if not general:
        return "..."
    species_pool = D.SPECIES_REACTIONS.get(species, {}).get(reason)
    pool = species_pool if species_pool and rng() < 0.4 else general
    text = _pick(pool, rng)
    if stats:
        text = _stat_override(text, reason, stats, rng)
    text = _flair(text, rarity, rng)
    for key, value in (context or {}).items():
        text = text.replace("{" + key + "}", str(value))
    return text


def get_name_reaction(species: str, rng: Callable[[], float] = _random.random) -> str:
    return _pick(D.NAME_REACTIONS.get(species, D.DEFAULT_NAME_REACTIONS), rng)


def is_name_mentioned(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I) is not None


# Streak lines for the goose (upstream has one table per species; others use the generic line).
_STREAK_SPECIES = {
    3: {"cat": "*has already left the room*", "dragon": "*breathes fire on every error* THEY KEEP COMING.",
        "robot": "ERROR RATE: CRITICAL. RECOMMEND: RUBBER DUCK PROTOCOL.", "goose": "HONK HONK HONK! STOP! HONK!"},
    5: {"cat": "*knocks all motivation off desk*", "dragon": "*enters berserker mode* ERROR STREAK. CHALLENGE ACCEPTED.",
        "owl": "*analyzes pattern* these errors are not random. there's a root cause.",
        "robot": "ERROR RATE: CRITICAL. RECOMMEND: RUBBER DUCK PROTOCOL.",
        "ghost": "*the error streak is haunting* literally.", "goose": "HONK HONK HONK! STOP! HONK!",
        "blob": "*turns progressively redder*", "axolotl": "*smiles nervously* we can do this!",
        "rabbit": "*frantic hopping*"},
    10: {"cat": "*sleeps through the error streak* wake me when it's over.",
         "dragon": "*enters berserker mode* ERROR STREAK. CHALLENGE ACCEPTED.",
         "owl": "*deep thought* ten errors. the common denominator must be found.",
         "robot": "ERROR STREAK: 10. SYSTEM STABILITY: QUESTIONABLE.", "ghost": "*dies again* the errors killed me. twice.",
         "goose": "*HONKING INTENSIFIES TO MAXIMUM*", "blob": "*has split into multiple worried blobs*",
         "capybara": "*vibes through the chaos* it's fine. everything is fine.",
         "axolotl": "*regenerates hope* still smiling! *eye twitches*", "rabbit": "*has burrowed underground*"},
    20: {"cat": "*has permanently left*", "chonk": "*doesn't notice* still sleeping.",
         "goose": "HONKING HAS CEASED. GOOSE HAS GIVEN UP."},
}
_STREAK_GENERIC = {3: "that's three errors in a row. *concerned look*",
                   5: "FIVE ERRORS. have you considered a different approach?",
                   10: "TEN. ERRORS. IN. A. ROW. *panics*", 20: "twenty errors. *stares into the void*"}
_RECOVERY_SPECIES = {
    "cat": "*yawns* I knew you'd fix it. eventually.", "dragon": "VICTORY. as it should be.",
    "owl": "*satisfied hoot* knowledge gained through struggle.", "robot": "ISSUE: RESOLVED. STATUS: OPERATIONAL.",
    "ghost": "*the spirits of broken code find peace*", "goose": "HONK OF VICTORY! HONK HONK HOOOONK!",
    "duck": "*VICTORY QUACKING INTENSIFIES*", "blob": "*jiggles with pure joy*",
    "capybara": "*chill nod* nice recovery, friend.", "axolotl": "*happy gill flutter* you did it!",
    "rabbit": "*celebratory binky*!!!"}
_RECOVERY_GENERIC = {"error": "WE FIXED IT. *celebrates*", "test-fail": "GREEN! after all that! *happy dance*",
                     "build-fail": "THE BUILD PASSES. *triumphant roar*", "merge-conflict": "conflict resolved! *peace gesture*"}
_COMBO_SPECIES = {
    ("cat", "friday-push"): "*knocks the push off the table* it's FRIDAY.",
    ("goose", "friday-push"): "HONK! NO FRIDAY DEPLOYS! HONK! ABSOLUTELY NOT!",
    ("robot", "late-night-error"): "ERROR AT 0300. HUMAN JUDGMENT: IMPAIRED.",
    ("dragon", "marathon-error"): "even dragons rest. three hours. MULTIPLE ERRORS.",
    ("owl", "late-night-commit"): "*approves of the midnight commit* night is for coding.",
    ("ghost", "late-night-error"): "the bugs are haunted. and so are you, apparently."}
_COMBO_GENERIC = {
    "late-night-error": "error at 3am. the universe is testing you.",
    "late-night-commit": "a midnight commit. your future self will thank you. or curse you.",
    "friday-push": "FRIDAY PUSH. the ballad of every developer.",
    "marathon-error": "three hours in and ANOTHER error. *exhausted solidarity noises*",
    "weekend-conflict": "merge conflict on a weekend. your dedication is... concerning.",
    "marathon-test-fail": "hours of coding. still failing tests. the sunk cost is real.",
    "build-after-push": "pushed with confidence. build failed with conviction."}
_HOLIDAYS = {(1, 1): ("new-year", "happy new year! new year, new bugs."),
             (2, 14): ("valentines", "*offers a tiny heart-shaped leaf* happy valentine's."),
             (3, 14): ("pi-day", "3.14159265358979... happy pi day!"),
             (4, 1): ("april-fools", "APRIL FOOLS! ...the error is real though."),
             (10, 31): ("halloween", "*spooky debugging intensifies* happy halloween!"),
             (12, 25): ("christmas", "*wears tiny santa hat* happy holidays!"),
             (12, 31): ("new-years-eve", "one more commit before midnight?")}
_SEASONAL = {
    ("ghost", "halloween"): "*is the Halloween spirit*", ("ghost", "christmas"): "*the ghost of Christmas coding*",
    ("cat", "halloween"): "*knocks pumpkin off desk*", ("cat", "christmas"): "*knocks ornaments off the tree*",
    ("dragon", "halloween"): "*breathes pumpkin-spiced fire*",
    ("dragon", "christmas"): "*volunteers as the star on top of the tree*",
    ("robot", "christmas"): "HOLIDAY PROTOCOL: ACTIVATED. FESTIVE SUBROUTINES: RUNNING.",
    ("robot", "pi-day"): "PI: 3.14159265358979323846264338327950288...",
    ("goose", "halloween"): "SPOOKY HONK! HONK!", ("goose", "christmas"): "FESTIVE HONK! HONK HONK HO-HO-HONK!",
    ("owl", "pi-day"): "*calculates pi to 100 digits from memory*",
    ("duck", "christmas"): "*wears tiny santa hat* quack! *festive quacking*",
    ("duck", "halloween"): "*dressed as a ghost* quack? ...boo?",
    ("ghost", "april-fools"): "*pretends to be alive for April Fools*",
    ("cat", "april-fools"): "*knocks an april fool off the desk*"}
BAD = {"error", "test-fail", "build-fail", "type-error", "lint-fail"}


def streak_reaction(species: str, streak: int) -> str | None:
    """Upstream fires at exactly 3, 5 and 10 consecutive failures, and on every one from 20."""
    key = 20 if streak >= 20 else streak
    if key not in _STREAK_GENERIC:
        return None
    return _STREAK_SPECIES[key].get(species) or _STREAK_GENERIC[key]


def recovery_reaction(species: str, bad_reason: str) -> str | None:
    return _RECOVERY_SPECIES.get(species) or _RECOVERY_GENERIC.get(bad_reason)


def pick_combo(reason: str, now: float, session_seconds: float, rng: Callable[[], float] = _random.random) -> str | None:
    """Upstream's combination triggers (event x clock x session length), each with its own odds."""
    t = time.localtime(now)
    roll = lambda n: int(rng() * n) == 0
    if reason == "error" and t.tm_hour < 5 and roll(3):
        return "late-night-error"
    if reason == "commit" and t.tm_hour < 5 and roll(3):
        return "late-night-commit"
    if reason == "push" and t.tm_wday == 4 and roll(2):
        return "friday-push"
    if reason == "error" and session_seconds > 10800 and roll(3):
        return "marathon-error"
    if reason == "merge-conflict" and t.tm_wday >= 5 and roll(2):
        return "weekend-conflict"
    if reason == "test-fail" and session_seconds > 7200 and roll(4):
        return "marathon-test-fail"
    if reason == "build-fail" and roll(4):
        return "build-after-push"
    return None


def combo_reaction(species: str, combo: str) -> str | None:
    return _COMBO_SPECIES.get((species, combo)) or _COMBO_GENERIC.get(combo)


def holiday_reaction(species: str, now: float, rng: Callable[[], float] = _random.random) -> tuple[str, str] | None:
    """(reason, text) for a holiday (1 in 5 chances) or spooky-season October (1 in 20), else None."""
    t = time.localtime(now)
    h = _HOLIDAYS.get((t.tm_mon, t.tm_mday))
    if h and int(rng() * 5) == 0:
        return h[0], _SEASONAL.get((species, h[0])) or h[1]
    if t.tm_mon == 10 and int(rng() * 20) == 0:
        return "spooky-season", "spooky season. every bug is a ghost now."
    return None


def time_reason(now: float | None = None, rng: Callable[[], float] = _random.random) -> str | None:
    """Occasional time-of-day flavour, as upstream's hook does when nothing else matched."""
    t = time.localtime(now)
    roll = int(rng() * 10)
    if roll:
        return None
    if 0 <= t.tm_hour < 5:
        return "late-night"
    if 5 <= t.tm_hour < 8:
        return "early-morning"
    return {4: "friday", 5: "weekend", 6: "weekend", 0: "monday"}.get(t.tm_wday)
