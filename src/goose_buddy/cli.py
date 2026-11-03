"""goose-buddy: the command line."""

from __future__ import annotations

import json
import sys
import time

from . import __version__, memory, menagerie, progress, state
from .brain import Brain, find_key
from .engine import STAT_NAMES, render_card
from . import feed
from .events import Watcher, default_db
from .identity import ensure_companion, hatch, resolve_user_id
from .pane import frame_lines, open_tmux_pane, run
from .reactions import REASON_TEXT
from .render import strip_ansi

HELP = """goose-buddy: your terminal companion

  show                 the companion card with the latest bubble
  stats                the stat card
  pet                  pet your buddy
  rename NAME          rename (1-14 chars)
  personality TEXT     set a custom personality
  mute | unmute        silence or restore reactions
  react REASON         trigger a reaction by hand (e.g. test-fail, commit, error)
  watch                the live animated pane, fed by your goose sessions
  pane                 open `watch` in a tmux side pane
  hatch --force        discard the companion and hatch again from current settings
  config [KEY=VALUE]   show or set settings (species_override=goose, brain.enabled=false ...)
  doctor               check identity, the session database and the brain
  xp | mood            level/XP card, current mood
  achievements [--all] the achievement list (secret ones stay hidden unless unlocked or --all)
  list                 the menagerie        save [SLOT] | summon [SLOT] | dismiss SLOT
  pick [SPECIES] [RARITY] [--name N]   roll a new buddy and add it to the menagerie
  memory [projects|bugs|preferences]   what the buddy remembers (local only)
  soul [--apply]       ask the brain for a name and personality (sends species/rarity/stats only)
  emit REASON [--source S]   append an event to the feed (anything can make the buddy react; see feed.py)
  brain-test           one real call to the brain model, to prove it works
"""


CHOICES = {"bubbleStyle": ("classic", "round"), "theme": ("dark", "light"),
           "species_pool": ("original", "coding-buddy")}
NON_NEGATIVE = ("commentCooldown", "reactionTTL", "suggestionCooldown")


def _set(cfg_path: str, value: str) -> dict:
    v: object = value
    if value.lower() in ("true", "false"):
        v = value.lower() == "true"
    else:
        try:
            v = int(value)
        except ValueError:
            try:
                v = float(value)
            except ValueError:
                pass
    keys = cfg_path.split(".")
    if cfg_path in CHOICES and v not in CHOICES[cfg_path]:
        raise ValueError(f"{cfg_path} must be one of {', '.join(CHOICES[cfg_path])}")
    if cfg_path in NON_NEGATIVE and not (isinstance(v, (int, float)) and v >= 0):
        raise ValueError(f"{cfg_path} must be a non-negative number")
    if len(keys) == 2:
        return state.save_config(**{keys[0]: {keys[1]: v}})
    return state.save_config(**{keys[0]: v})


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "show"
    if cmd in ("-h", "--help", "help"):
        state.bump("helps")
        print(HELP)
        return 0
    if cmd == "--version":
        print(__version__)
        return 0
    companion, created = ensure_companion()
    if created:
        print(f"🥚 {companion.name} hatched: {companion.bones.rarity} {companion.bones.species}!")

    counters = {"show": "shows", "stats": "shows", "rename": "renames", "personality": "personalities_set",
                "mute": "mutes", "unmute": "unmutes", "list": "lists"}
    if cmd in counters:
        state.bump(counters[cmd])
    rc = _dispatch(cmd, args, companion)
    fresh = progress.check_achievements(companion)
    for a in fresh:
        print(f"\n{a[3]} Achievement unlocked: {a[1]}! ({a[2]})")
    return rc


def _dispatch(cmd: str, args: list[str], companion) -> int:
    if cmd == "show":
        print("\n".join(strip_ansi(l) if not sys.stdout.isatty() else l for l in frame_lines(78)))
        print(f"\n  {companion.personality}")
    elif cmd == "stats":
        print("\n".join(render_card(companion.bones)))
        print(f"\n  {companion.name}: {companion.personality}")
    elif cmd == "pet":
        r = Watcher(companion).react("pet", force=True)
        print(f"{companion.name} {r['reaction'] if r else 'leans into it.'}")
        for n in (r or {}).get("notes", []):
            print(f"\n{n}")
    elif cmd == "rename" and len(args) > 1:
        name = " ".join(args[1:]).strip()
        if not 1 <= len(name) <= 14:
            print("names are 1-14 characters", file=sys.stderr)
            return 2
        companion.name = name
        state.save_companion(companion)
        print(f"renamed to {name}")
    elif cmd == "personality" and len(args) > 1:
        companion.personality = " ".join(args[1:])[:500]
        state.save_companion(companion)
        print("personality updated")
    elif cmd in ("mute", "unmute"):
        state.save_config(muted=(cmd == "mute"))
        print(f"reactions {'muted' if cmd == 'mute' else 'on'}")
    elif cmd == "react" and len(args) > 1:
        r = Watcher(companion, brain=Brain()).react(args[1], force=True)
        print(f"{companion.name}: {r['reaction']}  [{r['source']}]" if r else "(muted)")
    elif cmd == "watch":
        run(watch=True)
    elif cmd == "pane":
        return open_tmux_pane()
    elif cmd == "hatch" and "--force" in args:
        fresh = hatch()
        fresh.name = companion.name
        state.save_companion(fresh)
        print(f"re-hatched: {fresh.bones.rarity} {fresh.bones.species}")
    elif cmd == "config":
        if len(args) > 1 and "=" in args[1]:
            k, v = args[1].split("=", 1)
            try:
                _set(k, v)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 2
            if k in ("species_override", "species_pool", "user_id"):
                # identity settings change who the buddy is: re-hatch from them, keeping the name
                fresh = hatch()
                fresh.name = companion.name
                state.save_companion(fresh)
                print(f"re-hatched: {fresh.bones.rarity} {fresh.bones.species}")
        print(json.dumps(state.load_config(), indent=2))
    elif cmd == "xp":
        print(progress.xp_card(companion))
    elif cmd == "mood":
        print(progress.mood_card(companion))
    elif cmd == "achievements":
        print(progress.achievements_card("--all" in args))
    elif cmd == "list":
        print(menagerie.format_listing())
    elif cmd in ("save", "summon", "dismiss", "pick"):
        try:
            if cmd == "save":
                print(f'{companion.name} saved to slot "{menagerie.save(args[1] if len(args) > 1 else None)}".')
            elif cmd == "summon":
                c = menagerie.summon(args[1] if len(args) > 1 else None)
                print(f"{c.name} arrives: {c.bones.rarity} {c.bones.species}")
            elif cmd == "dismiss" and len(args) > 1:
                print(f'dismissed "{menagerie.dismiss(args[1])}"')
            elif cmd == "pick":
                rest = [a for a in args[1:] if not a.startswith("--")]
                name = args[args.index("--name") + 1] if "--name" in args else None
                if name in rest:
                    rest.remove(name)
                from .engine import ALL_SPECIES, RARITIES
                c = menagerie.pick(next((a for a in rest if a in ALL_SPECIES), None),
                                   next((a for a in rest if a in RARITIES), None), name)
                print(f"{c.name} hatches: {c.bones.rarity} {c.bones.species}")
            else:
                print("dismiss needs a slot", file=sys.stderr)
                return 2
        except menagerie.MenagerieError as e:
            print(str(e), file=sys.stderr)
            return 1
    elif cmd == "memory":
        print(json.dumps(memory.query(args[1] if len(args) > 1 else "all"), indent=2, ensure_ascii=False))
    elif cmd == "doctor":
        uid = resolve_user_id()
        cfg = state.load_config()
        print(f"identity   : {uid[:4]}…{uid[-2:]} ({len(uid)} chars)  seed source: "
              f"{'config/env' if cfg.get('user_id') else '~/.claude.json'}")
        print(f"buddy      : {companion.name}, {companion.bones.rarity} {companion.bones.species} "
              f"(pool={cfg['species_pool']}, override={cfg['species_override'] or 'none'})")
        print(f"sessions db: {default_db()} {'ok' if default_db().exists() else 'MISSING'}")
        print(f"brain      : {cfg['brain']['model']} enabled={cfg['brain']['enabled']} "
              f"key={'found' if find_key() else 'MISSING'} allowed_now={Brain().allowed()}")
        print(f"feed       : {feed.feed_path()} ({'exists' if feed.feed_path().exists() else 'not created yet'})")
        print(f"state dir  : {state.home()}")
    elif cmd == "emit" and len(args) > 1:
        src = args[args.index("--source") + 1] if "--source" in args and args.index("--source") + 1 < len(args) else ""
        if not feed.emit(args[1], src):
            print(f"unknown reason {args[1]!r}; known: {', '.join(sorted(feed.REASONS))}", file=sys.stderr)
            return 2
        print(f"queued {args[1]} -> {feed.feed_path()}")
    elif cmd == "soul":
        got = Brain().soul(companion.bones)
        if not got:
            print("no proposal (key missing, rate-limited, or the reply was unusable)", file=sys.stderr)
            return 1
        print(f"name: {got[0]}\npersonality: {got[1]}")
        if "--apply" in args:
            companion.name, companion.personality = got
            state.save_companion(companion)
            state.bump("renames")
            state.bump("personalities_set")
            print("applied")
        else:
            print("(run `goose-buddy soul --apply` to keep it)")
    elif cmd == "brain-test":
        t = time.time()
        line = Brain().react(companion, "test-fail")
        print(f"{line!r}  ({time.time() - t:.1f}s)" if line else "no reply (key missing, rate-limited, or the call failed)")
        return 0 if line else 1
    else:
        print(HELP)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
