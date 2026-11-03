"""Terminal rendering: sprite frames, the hero widget (bubble + sprite), colors.

Ported from coding-buddy's adapters/shared/widget-layout.ts and server/art.ts. The widget is
the same composition on every surface: a dashed speech bubble on the left, a `--` tail
pointing at the rarity-coloured sprite on the right, the companion's name centred underneath,
re-fitted to the terminal width on every render. When the sprite cannot fit beside a bubble,
the bubble is dropped as a unit.
"""

from __future__ import annotations

import re
import time

from .data.art_data import HAT_ART, SPECIES_ART
from .engine import RARITY_STARS, Companion, resolve_eye_glyph

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
RESET = "\x1b[0m"
DIM_ITALIC = "\x1b[2;3m"
ART_GAP = "  "
WIDGET_MAX_LINES = 10
DEFAULT_WIDTH = 78

THEME_COLORS = {
    "dark": {
        "common": "\x1b[38;2;153;153;153m", "uncommon": "\x1b[38;2;78;186;101m",
        "rare": "\x1b[38;2;177;185;249m", "epic": "\x1b[38;2;175;135;255m",
        "legendary": "\x1b[38;2;255;193;7m",
    },
    "light": {
        "common": "\x1b[38;2;90;90;90m", "uncommon": "\x1b[38;2;22;115;55m",
        "rare": "\x1b[38;2;55;85;210m", "epic": "\x1b[38;2;110;55;200m",
        "legendary": "\x1b[38;2;180;120;0m",
    },
}

WYVERN_HAT = {
    "crown": "} \\^^^/ {", "tophat": "} [___] {", "propeller": "}  -+-  {", "halo": "} (   ) {",
    "wizard": "}  /^\\  {", "beanie": "} (___) {", "tinyduck": "}  ,>   {",
}

# The idle cycle: mostly frame 0, small fidgets, and a blink (index 3 = frame 0 with '-' eyes).
FRAME_SEQUENCE = (0, 0, 0, 0, 1, 0, 0, 0, 3, 0, 0, 2, 0, 0, 0)


def rarity_color(rarity: str, theme: str = "dark") -> str:
    return THEME_COLORS.get(theme, THEME_COLORS["dark"])[rarity]


# ─── Display width (kept in step with upstream's char_width) ───────────────────────────────

_EMOJI_RANGES = ((0x1F300, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F900, 0x1FAFF), (0x2728, 0x2728),
                 (0x2B50, 0x2B50), (0x2705, 0x2705), (0x274C, 0x274C), (0x23E9, 0x23F3))


def _char_width(ch: str, nxt: str = "") -> int:
    cp = ord(ch)
    if ch == "\u200d" or 0xFE00 <= cp <= 0xFE0F:
        return 0
    if nxt == "\ufe0f" and cp > 0x2000:
        return 2
    if any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES):
        return 2
    if 0x2500 <= cp <= 0x25BF:      # box drawing / blocks sit in the CJK span but are narrow
        return 1
    if 0x3000 <= cp <= 0x9FFF or 0xFF01 <= cp <= 0xFF60:
        return 2
    return 1


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def display_width(text: str) -> int:
    chars = list(strip_ansi(text))
    return sum(_char_width(c, chars[i + 1] if i + 1 < len(chars) else "") for i, c in enumerate(chars))


def truncate_to_width(text: str, width: int) -> str:
    if width <= 0:
        return ""
    out, cur = [], 0
    for ch in strip_ansi(text):
        w = _char_width(ch)
        if cur + w > width:
            break
        out.append(ch)
        cur += w
    return "".join(out)


def pad_line(line: str, width: int) -> str:
    bounded = truncate_to_width(line, width) if display_width(line) > width else line
    return bounded + " " * max(0, width - display_width(bounded))


def center_to_width(text: str, width: int) -> str:
    bounded = truncate_to_width(text, width)
    rest = max(0, width - display_width(bounded))
    left = rest // 2
    return " " * left + bounded + " " * (rest - left)


def wrap_reaction(reaction: str, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in f"\U0001F4AC {reaction.strip()}".split():
        if display_width(word) > width:
            if current:
                lines.append(current)
            lines.append(truncate_to_width(word, max(1, width - 1)) + "\u2026")
            current = ""
            continue
        candidate = f"{current} {word}" if current else word
        if display_width(candidate) > width:
            if current:
                lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return lines
    bounded = lines[:max_lines]
    bounded[-1] = truncate_to_width(bounded[-1], max(1, width - 1)) + "\u2026"
    return bounded


# ─── Sprite frames ──────────────────────────────────────────────────────────────────────────

def art_frame(species: str, eye: str, frame: int = 0, hat: str = "none", raw_eye: bool = False) -> list[str]:
    """One 5-row sprite frame with the eye substituted and the hat overlaid on the top row."""
    frames = SPECIES_ART[species]
    glyph = eye if raw_eye else resolve_eye_glyph(eye)
    art = [line.replace("{E}", glyph) for line in frames[frame % len(frames)]]
    if hat != "none":
        if species == "wyvern":
            art[0] = WYVERN_HAT.get(hat, art[0])
        elif not art[0].strip():
            art[0] = HAT_ART[hat]
    return art


def animated_frame(species: str, eye: str, hat: str, tick: int) -> list[str]:
    idx = FRAME_SEQUENCE[tick % len(FRAME_SEQUENCE)]
    if idx == 3:                       # blink: frame 0 with literal '-' eyes
        return art_frame(species, "-", 0, hat, raw_eye=True)
    return art_frame(species, eye, idx, hat)


def compact_frame(frame_rows: list[str]) -> list[str]:
    """First three non-empty rows (keeps a hat on top, otherwise face and body)."""
    rows = [r for r in frame_rows if r.strip()][:3]
    return rows + [""] * (3 - len(rows))


# ─── The hero widget ────────────────────────────────────────────────────────────────────────

def _frame_reaction(reaction: str, achievements: list[str], inner: int, style: str = "classic") -> list[str]:
    max_content = WIDGET_MAX_LINES - 2
    ach_lines = [f"\U0001F3C6 {a}" for a in achievements]
    reaction_max = max(0, max_content - len(ach_lines))
    reaction_lines = wrap_reaction(reaction.strip(), inner, reaction_max) if reaction.strip() else []
    content = (reaction_lines + ach_lines)[:max_content]
    if len(content) == 1:
        content = [""] + content
    if style == "round":
        top, bottom, edge = "(" + "~" * (inner + 2) + ")", "(" + "~" * (inner + 2) + ")", ("(", ")")
    else:
        top, bottom, edge = "." + "-" * (inner + 2) + ".", "`" + "-" * (inner + 2) + "'", ("|", "|")
    return [top, *[f"{edge[0]} {DIM_ITALIC}{pad_line(line, inner)}{RESET} {edge[1]}" for line in content], bottom]


def render_widget(
    companion: Companion,
    reaction: str | None = None,
    achievements: list[str] | None = None,
    width: int = DEFAULT_WIDTH,
    tick: int = 0,
    theme: str = "dark",
    sprite_rows: list[str] | None = None,
    bubble_style: str = "classic",
) -> list[str]:
    """The companion card: bubble on the left, sprite on the right, name underneath."""
    achievements = achievements or []
    safe_width = max(1, int(width))
    b = companion.bones
    art = [row.rstrip() for row in (sprite_rows or animated_frame(b.species, b.eye, b.hat, tick))]
    color = rarity_color(b.rarity, theme)
    label = f"{companion.name} {RARITY_STARS[b.rarity]}{' ✨' if b.shiny else ''}"
    art_width = max((display_width(r) for r in art), default=0)
    sprite_width = min(safe_width, max(art_width, display_width(label)))
    clipped_art = [f"{color}{pad_line(r, sprite_width)}{RESET}" for r in art]
    clipped_label = f"{color}{center_to_width(label, sprite_width)}{RESET}"

    min_inner, tail_width = 12, 3
    max_inner = safe_width - sprite_width - tail_width - 4
    has_content = bool((reaction or "").strip()) or bool(achievements)
    can_frame = has_content and max_inner >= min_inner
    inner = max_inner if can_frame else 0
    bubble = _frame_reaction(reaction or "", achievements, inner, bubble_style) if can_frame else []
    bubble_width = inner + 4
    card_width = bubble_width + tail_width + sprite_width if can_frame else sprite_width

    ach_sprites = [] if can_frame else [f"{DIM_ITALIC}{pad_line(f'🏆 {a}', sprite_width)}{RESET}" for a in achievements]
    body_rows = [*clipped_art, clipped_label, *ach_sprites]
    height = max(len(body_rows), len(bubble))
    connector = min(len(bubble) - 2, max(1, len(bubble) // 2)) if can_frame else -1

    lines = []
    for i in range(min(WIDGET_MAX_LINES, height)):
        sprite = pad_line(body_rows[i] if i < len(body_rows) else "", sprite_width)
        if can_frame:
            bubble_line = bubble[i] if i < len(bubble) else " " * bubble_width
            body = f"{bubble_line}{'-- ' if i == connector else '   '}{sprite}"
        else:
            body = sprite
        lines.append(" " * max(0, safe_width - card_width) + body)
    return lines


def current_tick(period: float = 0.7) -> int:
    return int(time.time() / period)
