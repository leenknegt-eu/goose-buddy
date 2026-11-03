# goose-buddy

A persistent terminal companion for [goose](https://github.com/block/goose): a port of
[coding-buddy](https://github.com/ramarivera/coding-buddy) (itself a continuation of Claude Code's
removed `/buddy`). 20 species of animated ASCII art, rarities, five stats, a personality, and speech
bubbles that react to what happens in your goose sessions.

```
 .--------------------------------------------------------.
 | 💬 HONK. the tests have spoken. and they said 'no'.    |--      (×>
 |                                                        |        ||
 `--------------------------------------------------------'     _(__)_
                                                                 ^^^^
                                                               Honk ★★
```

## How it works

* **The buddy hears your session by reading goose's own database** (`~/.local/share/goose/sessions/sessions.db`,
  read-only). Tool errors, failed tests, commits, merge conflicts and so on are recognised *locally*. This costs
  the agent nothing and needs no cooperation from it.
* **A tmux side pane** (`goose-buddy pane`) draws the animated sprite and bubble, re-fitted to the pane width.
* **The brain** is a free OpenCode Go model (`beam-free`). Each reaction is first a canned line from the original
  pools (instant), then upgraded to a line written by the model in the buddy's voice if it answers in time.
* **An MCP extension** (`goose-buddy-mcp`) lets you and the agent talk to the buddy: show, stats, pet, rename,
  personality, mute.

## Privacy: what the model sees

Only the buddy's persona and a fixed one-line description of an event class, such as `event: the tests just
failed`. **Never** tool output, commands, file names or code (a test plants a canary in the session and checks it
never appears in a request). Beam Free is documented as *not used for training*, with 30-day retention;
`iceman-contributor-free` trains on prompts and is deliberately not used.

OpenCode Go is meant for "typical coding agent traffic". The buddy identifies itself (`goose-buddy/x.y`), sends a
stable `x-opencode-session`, and stays low-volume (minimum 25 s between calls, 40 per hour). If you would rather not
use the model at all: `goose-buddy config brain.enabled=false`. Canned lines still work.

## Identity: "the same buddy as before"

Like the original, the buddy is a pure function of your Claude account id (read from `~/.claude.json`, used only
as a local seed) via wyhash and mulberry32 with salt `friend-2026-401`. The Python port reproduces coding-buddy's
golden test vectors exactly.

**A fidelity bug upstream.** coding-buddy added `wyvern` and `pikachu` to the pool the generator picks from. Species
is `pool[floor(rng * len(pool))]`, so going from 18 to 20 entries silently changes the species of almost everyone.
Every other roll (rarity, eyes, hat, stats) is unaffected. goose-buddy keeps the original 18 by default
(`species_pool=original`); `species_pool=coding-buddy` gives upstream's behaviour, and `species_override=goose` forces
a species while keeping all other rolls.

## Install

```sh
uv tool install .          # goose-buddy, goose-buddy-mcp
goose-buddy doctor
goose-buddy pane           # inside tmux; or `goose-buddy watch` in a spare terminal
```

Register the extension in `~/.config/goose/config.yaml`:

```yaml
extensions:
  buddy:
    enabled: true
    type: stdio
    name: buddy
    cmd: goose-buddy-mcp
    args: []
    timeout: 60
```

## Commands

`show` `stats` `pet` `rename NAME` `personality TEXT` `mute` `unmute` `react REASON` `watch` `pane`
`xp` `mood` `achievements [--all]` `list` `save [SLOT]` `summon [SLOT]` `dismiss SLOT` `pick [SPECIES] [RARITY] [--name N]`
`memory [projects|bugs|preferences]` `hatch --force` `config [KEY=VALUE]` `doctor` `brain-test`

## Status

Done: engine, art and animation, widget, reactions, session watcher, brain, tmux pane, CLI, MCP extension,
XP/levels/upgrades, moods, all 131 achievements, menagerie, suggestions, local project/bug/preference memory.
Not yet ported: species-specific streak/combo/seasonal lines (goose only; others use generic lines), the
`frequency`/`style`/`statusline` settings, brain-written name/personality at hatch, and the code-analysis
suggestions (long function, new file without test, rename sequence).
Every achievement counter is now fed. Upstream never feeds `holiday_sessions`, `spooky_sessions` or
`april_fools_errors` (those three achievements are unreachable there); goose-buddy derives them from the date.

## Tests

`pytest` (104 tests: golden vectors, rendering never overflows its width, classifier, the brain's privacy contract
and API etiquette, the watcher against a session database, concurrent state access).
