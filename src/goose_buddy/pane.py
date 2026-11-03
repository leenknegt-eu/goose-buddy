"""The live display: the animated widget in a terminal pane, fed by the session watcher."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import progress, state
from .brain import Brain
from .events import Watcher
from .identity import ensure_companion
from .render import current_tick, render_widget

CLEAR_HOME = "\x1b[H"
HIDE, SHOW = "\x1b[?25l", "\x1b[?25h"


def frame_lines(width: int | None = None) -> list[str]:
    companion, _ = ensure_companion()
    cfg = state.load_config()
    r = state.live_reaction()
    cols = width or shutil.get_terminal_size((38, 12)).columns
    a = state.kv_get("announce", None)
    notes = a["notes"] if a and time.time() - a["timestamp"] <= cfg["reactionTTL"] * 2 else []
    lines = render_widget(companion, r["reaction"] if r else None, notes, max(10, cols - 1),
                          current_tick(), cfg["theme"])
    xp = progress.xp_state()
    return lines + ["", f" {progress.MOOD_FACES[progress.get_mood()['current']]} {progress.xp_bar(xp['totalXp'], 12)}"
                        f"  {xp['totalXp']} xp"]


def run(watch: bool = True) -> None:
    """Draw until interrupted; when `watch`, poll goose's session database in a thread."""
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    companion, _ = ensure_companion()

    def poller() -> None:
        w = Watcher(companion, brain=Brain(), new_session=True)
        while not stop.is_set():
            try:
                w.poll()
            except Exception:                    # the display must outlive any bad row
                pass
            stop.wait(1.5)

    if watch:
        threading.Thread(target=poller, daemon=True).start()
    sys.stdout.write(HIDE + "\x1b[2J")
    try:
        while not stop.is_set():
            lines = frame_lines()
            sys.stdout.write(CLEAR_HOME + "\n".join(line + "\x1b[K" for line in lines) + "\x1b[J")
            sys.stdout.flush()
            stop.wait(0.7)
    finally:
        sys.stdout.write(SHOW + "\n")


def open_tmux_pane(width: int = 40) -> int:
    """Split a side pane running `goose-buddy watch`, leaving focus where it was."""
    if not os.environ.get("TMUX"):
        print("not inside tmux: run `goose-buddy watch` in a spare terminal instead", file=sys.stderr)
        return 1
    exe = shutil.which("goose-buddy") or f"{sys.executable} -m goose_buddy.cli"
    return subprocess.call(["tmux", "split-window", "-h", "-d", "-l", str(width), f"{exe} watch"])
