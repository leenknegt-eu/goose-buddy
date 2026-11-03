"""goose-buddy as a goose extension (MCP, stdio): lets the agent and the user talk to the buddy.

The buddy already reacts on its own (the watcher reads the session), so these tools are for
conversation: showing, petting, renaming, muting, and an explicit reaction. The extension's
instructions tell the agent NOT to call buddy_react every turn (that is what the watcher is for).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

import json

from . import __version__, memory, menagerie, progress, state
from .engine import render_card
from .events import Watcher
from .identity import ensure_companion
from .pane import frame_lines
from .render import strip_ansi

mcp = MCPServer(
    name="buddy", version=__version__,
    instructions=(
        "A small companion creature lives in the user's terminal and reacts to the session on its own. "
        "Do not call buddy_react after every turn. Use these tools only when the user addresses the "
        "buddy, asks to pet it, rename it, mute it, or asks how it is doing. Keep the buddy's name and "
        "personality in mind but never let it get in the way of the task."
    ),
)


def _companion():
    return ensure_companion()[0]


@mcp.tool()
async def buddy_show() -> str:
    """Show the buddy: sprite, name, rarity, and its current speech bubble."""
    c = _companion()
    state.bump("shows")
    return "\n".join(strip_ansi(l) for l in frame_lines(78)) + f"\n\n{c.personality}"


@mcp.tool()
async def buddy_stats() -> str:
    """Show the buddy's stat card (DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK)."""
    return "\n".join(render_card(_companion().bones))


@mcp.tool()
async def buddy_pet() -> str:
    """Pet the buddy."""
    c = _companion()
    r = Watcher(c).react("pet", force=True)
    return f"{c.name} {r['reaction'] if r else 'leans into it.'}" + "".join(f"\n{n}" for n in (r or {}).get("notes", []))


@mcp.tool()
async def buddy_react(reason: str = "turn") -> str:
    """Make the buddy react to an event class (e.g. test-fail, commit, error, success)."""
    c = _companion()
    r = Watcher(c).react(reason, force=False)
    return r["reaction"] if r else "(the buddy stays quiet)"


@mcp.tool()
async def buddy_rename(name: str) -> str:
    """Rename the buddy (1-14 characters)."""
    name = name.strip()
    if not 1 <= len(name) <= 14:
        return "names are 1-14 characters"
    c = _companion()
    c.name = name
    state.save_companion(c)
    state.bump("renames")
    return f"renamed to {name}"


@mcp.tool()
async def buddy_set_personality(personality: str) -> str:
    """Set the buddy's personality text (up to 500 characters)."""
    c = _companion()
    c.personality = personality.strip()[:500]
    state.save_companion(c)
    state.bump("personalities_set")
    return "personality updated"


@mcp.tool()
async def buddy_mute(muted: bool = True) -> str:
    """Mute (or unmute) the buddy's reactions."""
    state.save_config(muted=muted)
    state.bump("mutes" if muted else "unmutes")
    return "muted" if muted else "unmuted"


def _notice() -> str:
    new = progress.check_achievements(_companion())
    return "".join(f"\n{a[3]} Achievement unlocked: {a[1]}!" for a in new)


@mcp.tool()
async def buddy_xp() -> str:
    """Level, XP, unlocked upgrades and reactions."""
    return progress.xp_card(_companion()) + _notice()


@mcp.tool()
async def buddy_mood() -> str:
    """The buddy's current mood and the stat shifts it brings."""
    return progress.mood_card(_companion())


@mcp.tool()
async def buddy_achievements(show_secret: bool = False) -> str:
    """List achievements; secret ones stay hidden until unlocked unless show_secret is true."""
    return progress.achievements_card(show_secret) + _notice()


@mcp.tool()
async def buddy_list() -> str:
    """List the saved buddies (the menagerie)."""
    state.bump("lists")
    return menagerie.format_listing() + _notice()


@mcp.tool()
async def buddy_save(slot: str = "") -> str:
    """Save the current buddy to a named slot (default: its name)."""
    try:
        return f'saved to slot "{menagerie.save(slot or None)}".' + _notice()
    except menagerie.MenagerieError as e:
        return str(e)


@mcp.tool()
async def buddy_summon(slot: str = "") -> str:
    """Switch to a saved buddy by slot name (omit for a random saved one)."""
    try:
        c = menagerie.summon(slot or None)
        return f"{c.name} arrives: {c.bones.rarity} {c.bones.species}." + _notice()
    except menagerie.MenagerieError as e:
        return str(e)


@mcp.tool()
async def buddy_dismiss(slot: str) -> str:
    """Remove a saved buddy (never the active one)."""
    try:
        return f'dismissed "{menagerie.dismiss(slot)}".' + _notice()
    except menagerie.MenagerieError as e:
        return str(e)


@mcp.tool()
async def buddy_pick(species: str = "", rarity: str = "", name: str = "") -> str:
    """Roll a new buddy (optionally of a given species/rarity/name); it joins the menagerie and becomes active."""
    try:
        c = menagerie.pick(species or None, rarity or None, name or None)
        return f"{c.name} hatches: {c.bones.rarity} {c.bones.species}." + _notice()
    except menagerie.MenagerieError as e:
        return str(e)


@mcp.tool()
async def buddy_memory(kind: str = "all") -> str:
    """What the buddy remembers locally: projects, bugs, preferences (kind: all|projects|bugs|preferences)."""
    return json.dumps(memory.query(kind), indent=2, ensure_ascii=False)


@mcp.tool()
async def buddy_resolve_bug(bug_id: str) -> str:
    """Mark a remembered bug as resolved."""
    b = memory.resolve_bug(bug_id)
    return f"resolved {bug_id}" + _notice() if b else f"no such bug {bug_id!r}"


@mcp.tool()
async def buddy_remember_preference(key: str, value: str, context: str = "") -> str:
    """Record a user preference the buddy should remember (confidence grows with repetition)."""
    p = memory.set_preference(key, value, context)
    return f"{key} = {p['value']} (confidence {p['confidence']:.1f})"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
