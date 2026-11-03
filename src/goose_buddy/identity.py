"""Who is this buddy for? Resolve the seed, then hatch (or load) the companion."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import state
from .data.reaction_data import FALLBACK_NAMES
from .engine import Companion, generate_bones, generate_personality


def resolve_user_id() -> str:
    """Explicit setting, else Claude Code's own account id (the original buddy's seed), else 'anon'."""
    explicit = os.environ.get("GOOSE_BUDDY_USER_ID") or state.load_config().get("user_id")
    if explicit:
        return str(explicit)
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    for p in ([Path(base) / ".claude.json"] if base else []) + [Path.home() / ".claude.json"]:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        uid = (d.get("oauthAccount") or {}).get("accountUuid") or d.get("userID")
        if uid:
            return str(uid)
    return "anon"


def _default_name(user_id: str) -> str:
    from .engine import _js_parse_int_hex
    return FALLBACK_NAMES[_js_parse_int_hex(user_id[:8]) % len(FALLBACK_NAMES)]


def hatch(user_id: str | None = None, name: str | None = None) -> Companion:
    """Build the companion from config + identity (pure; does not save)."""
    cfg = state.load_config()
    uid = user_id or resolve_user_id()
    bones = generate_bones(uid, species_pool=cfg["species_pool"])
    if cfg.get("species_override"):
        bones.species = cfg["species_override"]
    return Companion(bones, name or _default_name(uid), generate_personality(bones, uid),
                     int(time.time() * 1000), uid)


def ensure_companion() -> tuple[Companion, bool]:
    c = state.load_companion()
    if c:
        return c, False
    c = hatch()
    state.save_companion(c)
    state.bump("hatches")
    return c, True
