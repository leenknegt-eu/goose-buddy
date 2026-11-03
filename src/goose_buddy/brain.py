"""The buddy's brain: one short in-character line from a free OpenCode Go model.

What this module will and will not do, by design:

* It sends ONLY a short English description of an event class ("the tests just failed", see
  reactions.REASON_TEXT) plus the buddy's own persona. Never tool output, file names, code,
  commands or anything from the session. (Beam Free keeps requests for 30 days, and the
  decomp project forbids uploading game code.)
* It identifies itself (`goose-buddy/x.y` user agent) and sends a stable `x-opencode-session`,
  as OpenCode Go's docs require, and stays low-volume: a minimum interval and an hourly cap.
* It never raises into the caller. No key, no network, a refusal or an empty reply all return
  None, and the caller shows a canned line instead.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable

import httpx

from . import __version__, state
from .engine import Companion
from .reactions import REASON_TEXT

URL = "https://opencode.ai/zen/go/v1/chat/completions"
USER_AGENT = f"goose-buddy/{__version__}"
MAX_LINE = 140


def find_key() -> str | None:
    """OPENCODE_API_KEY from the environment, a key file in the buddy home, or goose's secrets."""
    key = os.environ.get("OPENCODE_API_KEY")
    if key:
        return key.strip()
    f = state.home() / "llm-key"
    if f.is_file():
        return f.read_text().strip() or None
    try:
        for line in (Path.home() / ".config" / "goose" / "secrets.yaml").read_text().splitlines():
            if line.startswith("OPENCODE_API_KEY:"):
                return line.split(":", 1)[1].strip().strip("'\"") or None
    except OSError:
        pass
    return None


def build_messages(c: Companion, reason: str) -> list[dict[str, str]]:
    """The whole prompt. Everything in it is persona data or a fixed reason description."""
    s = ", ".join(f"{k} {v}" for k, v in c.bones.stats.items())
    system = (
        f"You are {c.name}, a {c.bones.rarity} {c.bones.species} who lives in a programmer's terminal. "
        f"Personality: {c.personality} Stats (0-100): {s}. High SNARK means sarcastic, high CHAOS "
        "unpredictable, high WISDOM insightful, high PATIENCE calm. "
        "React to what just happened in ONE short line (max 14 words), in character, lowercase, "
        "stage directions like *this* allowed, no emojis, no quotation marks. Output only the line."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"event: {REASON_TEXT.get(reason, 'something happened')}"}]


def clean(text: str) -> str | None:
    t = re.sub(r"\s+", " ", (text or "").strip()).strip("\"'`")
    return t[:MAX_LINE] if len(t) >= 2 else None


class Brain:
    def __init__(self, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._transport = transport
        self._clock = clock

    # -- budget -----------------------------------------------------------------------------
    def _session_id(self) -> str:
        b = state.kv_get("brain", {})
        day = time.strftime("%Y-%m-%d", time.gmtime(self._clock()))
        if b.get("session_day") != day or not b.get("session_id"):
            b.update(session_day=day, session_id=str(uuid.uuid4()))
            state.kv_set("brain", b)
        return b["session_id"]

    def allowed(self) -> bool:
        cfg = state.load_config()["brain"]
        if not cfg["enabled"] or find_key() is None:
            return False
        now = self._clock()
        calls = [t for t in state.kv_get("brain", {}).get("calls", []) if now - t < 3600]
        return bool(not calls or (now - calls[-1] >= cfg["minInterval"] and len(calls) < cfg["maxPerHour"]))

    def _record_call(self) -> None:
        b = state.kv_get("brain", {})
        now = self._clock()
        b["calls"] = [t for t in b.get("calls", []) if now - t < 3600] + [now]
        state.kv_set("brain", b)

    # -- the call ---------------------------------------------------------------------------
    def react(self, c: Companion, reason: str) -> str | None:
        if not self.allowed():
            return None
        cfg = state.load_config()["brain"]
        self._record_call()                      # count attempts, so failures can't hammer the API
        headers = {"Authorization": f"Bearer {find_key()}", "Content-Type": "application/json",
                   "User-Agent": USER_AGENT, "x-opencode-session": self._session_id()}
        body = {"model": cfg["model"], "messages": build_messages(c, reason),
                "max_tokens": 800, "reasoning_effort": "low"}
        try:
            with httpx.Client(timeout=cfg["timeout"], transport=self._transport) as client:
                r = client.post(URL, json=body, headers=headers)
            if r.status_code != 200:
                return None
            return clean(r.json()["choices"][0]["message"].get("content") or "")
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return None
