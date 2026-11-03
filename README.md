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

Only the buddy's persona, a fixed one-line description of an event class (`event: the tests just failed`), a few
fixed context phrases ("a decompilation worker submitted a function", burst counts, failure streaks, mood), a random
style angle, and the buddy's own last few lines (so it does not repeat itself). **Never** tool output, commands, file names or code (a test plants a canary in the session and checks it
never appears in a request). Beam Free is documented as *not used for training*, with 30-day retention;
`iceman-contributor-free` trains on prompts and is deliberately not used.

OpenCode Go is meant for "typical coding agent traffic". The buddy identifies itself (`goose-buddy/x.y`), sends a
stable `x-opencode-session`, and stays low-volume (minimum 10 s between calls, 120 per hour; tune with `brain.minInterval` and `brain.maxPerHour`). If you would rather not
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
`memory [projects|bugs|preferences]` `soul [--apply]` `hatch --force` `config [KEY=VALUE]` `doctor` `brain-test`

## Status

Done: engine, art and animation, widget, reactions, session watcher, brain, tmux pane, CLI, MCP extension,
XP/levels/upgrades, moods, all 131 achievements (every counter is fed), menagerie, suggestions (including
long function, new file without a test and rename sequences, judged locally from tool output), local
project/bug/preference memory, species-specific streak/recovery/combo/holiday lines, `bubbleStyle`
(classic or round), settings toggles (`moodEnabled`, `suggestionsEnabled`, `suggestionCooldown`,
`commentCooldown`) and `soul`, which asks the brain for a name and personality (species, rarity and stats
only; it proposes, `--apply` keeps it).
Authored lines (streaks, recoveries, combos, holidays) are never overwritten by the brain.
Upstream never feeds `holiday_sessions`, `spooky_sessions` or `april_fools_errors` (those three achievements
are unreachable there); goose-buddy derives them from the date.
Not applicable here: the `statusline` settings (goose has no status line; the tmux pane replaces it).

## The event feed (anything can make the buddy react)

The buddy hears every goose session that gets saved: yours, recipe runs and the decomp workers (they are stored
as `hidden` sessions). For events that never appear in a transcript, or that come from non-goose tools, there is
a feed: an append-only JSON-lines file, `$GOOSE_BUDDY_HOME/feed.jsonl` (or `$GOOSE_BUDDY_FEED`).

    goose-buddy emit all-green --source ci
    echo '{"reason":"deploy","source":"cron"}' >> ~/.config/goose/buddy/feed.jsonl

`reason` must be one of the buddy's reason codes (`test-fail`, `error`, `all-green`, `commit`, `push`, `deploy`,
`release`, `merge-conflict`, `build-fail`, ... see `goose-buddy emit` with a bad reason for the list). The feed is
treated as untrusted: lines are capped at 512 bytes, parsed strictly, unknown reasons are dropped, and `source`
is a label that is never sent to the model (a test plants a canary label to prove it). A new watcher starts at
the end of the file. Writers rotate the file at 256 KB. Events go through the same cooldown, XP, mood and
achievements as everything else.

The decomp orchestrator is the first writer (`decomp_orchestrator.buddyfeed`): verdicts (verified is a win,
a strike an error, needs-review a soft failure), submissions, give-ups and safety drains, with only the reason
and the event kind as the label, never addresses or details. It does nothing unless the buddy is installed, and
`DECOMP_BUDDY_FEED=0` turns it off.

## Updating

On the machine that runs it: `scripts/update.sh` (fast-forward pull, reinstall, `doctor`). It refuses to run
over local changes and never touches goose's config or the buddy's state. For a devbox reached through an ssh
alias that attaches tmux, run it in the container, for example:

    ssh -o RemoteCommand=none -o RequestTTY=no devbox 'docker exec -u dev devbox-dev /work/GitHub/goose-buddy/scripts/update.sh'

A running pane and already-running goose sessions keep the old code until restarted.

## Tests

`pytest` (126 tests: golden vectors, rendering never overflows its width, classifier, the brain's privacy contract
and API etiquette, the watcher against a session database, concurrent state access).


## Variety
Lines should not repeat: the last 12 shown lines are remembered, canned picks re-roll away from them, the model is
told what it already said (and its reply is dropped if it repeats one), and each call gets a random style angle at
temperature 1.0. Bursts of the same event (a worker pool committing all day) go quiet after the third one in ten
minutes, then get a summary line on every fifth ("7 commits in 10 minutes").

## Quiet sessions
The tool-output classifier reads human coding sessions: `error:`, `push`, `v1.2` and `To …:` mean git and CI. Machine
workloads break that (a decompilation worker's compiler errors and `push ebp` disassembly looked like a failing
build and a git push, and made the buddy frantic). Sessions whose name starts with one of `quietSessions` are ignored
by the watcher; the default is the decomp worker and head. Their meaningful events (submissions, verdicts, give-ups,
errors) come through the [event feed](#event-feed) instead. Change it with
`goose-buddy config quietSessions='["Name prefix", ...]'`; `[]` hears everything.
