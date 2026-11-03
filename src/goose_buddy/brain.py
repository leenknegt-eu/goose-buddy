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
import random as _random
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


#: a different angle each time, so identical events do not get identical jokes
ANGLES = ("deadpan understatement", "dramatic overreaction", "a goose-themed pun", "a tiny two-line rhyme",
          "a question to the user", "mock-formal announcement", "a gentle encouragement", "a suspicious side-eye",
          "a made-up statistic", "a grudging compliment", "a threat to honk", "a nature-documentary narration",
          "a sports commentator", "a weary sigh", "pure excitement", "a cryptic proverb")

#: what the feed's `source` label may add to the prompt. Fixed phrases only: the label itself is never sent.
SOURCE_TEXT = {
    "decomp:submitted": "a decompilation worker submitted a function", "decomp:verdict": "a verdict came back from the server",
    "decomp:gave_up": "a worker gave up on a function", "decomp:safety_giveup": "the safety net gave up on a function",
    "decomp:auto_drain": "the pool drained itself to be safe", "decomp:worker_error": "a worker crashed",
    "decomp:packet_error": "a worker's packet was malformed", "decomp:rotate_failed": "a worker rotation failed",
    "decomp:reclaim_failed": "reclaiming a claim failed", "decomp:llm_keys_exhausted": "the llm keys ran out",
    "decomp:no_llm_key": "no llm key was available",
}


def source_phrase(source: str) -> str | None:
    return SOURCE_TEXT.get(source)


def similar(a: str, b: str) -> bool:
    """True when two lines are the same or share most of their words."""
    wa, wb = set(re.findall(r"[a-z']+", a.lower())), set(re.findall(r"[a-z']+", b.lower()))
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.7


def build_messages(c: Companion, reason: str, facts: list[str] | None = None, recent: list[str] | None = None,
                   angle: str | None = None) -> list[dict[str, str]]:
    """The whole prompt. Everything in it is persona data, a fixed reason description, counts or the buddy's own lines."""
    s = ", ".join(f"{k} {v}" for k, v in c.bones.stats.items())
    system = (
        f"You are {c.name}, a {c.bones.rarity} {c.bones.species} who lives in a programmer's terminal. "
        f"Personality: {c.personality} Stats (0-100): {s}. High SNARK means sarcastic, high CHAOS "
        "unpredictable, high WISDOM insightful, high PATIENCE calm. "
        "React to what just happened in ONE short line (max 14 words), in character, lowercase, "
        "stage directions like *this* allowed, no emojis, no quotation marks. Output only the line."
    )
    if angle:
        system += f" This time, go for: {angle}."
    if recent:
        system += (" You already said these lines recently. Say something clearly different: no repeated "
                   "words, jokes or sentence shapes. Lines: " + " | ".join(recent[-8:]))
    event = f"event: {REASON_TEXT.get(reason, 'something happened')}"
    if facts:
        event += ". context: " + "; ".join(facts)
    return [{"role": "system", "content": system}, {"role": "user", "content": event}]


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
    def _chat(self, messages: list[dict[str, str]], clean_fn=None) -> str | None:
        """One budgeted call; None on any failure. `messages` must hold persona data only."""
        if not self.allowed():
            return None
        cfg = state.load_config()["brain"]
        self._record_call()                      # count attempts, so failures can't hammer the API
        headers = {"Authorization": f"Bearer {find_key()}", "Content-Type": "application/json",
                   "User-Agent": USER_AGENT, "x-opencode-session": self._session_id()}
        body = {"model": cfg["model"], "messages": messages, "max_tokens": 800, "reasoning_effort": "low",
                "temperature": 1.0}
        try:
            with httpx.Client(timeout=cfg["timeout"], transport=self._transport) as client:
                r = client.post(URL, json=body, headers=headers)
            if r.status_code != 200:
                return None
            return r.json()["choices"][0]["message"].get("content") or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return None

    def react(self, c: Companion, reason: str, facts: list[str] | None = None, recent: list[str] | None = None,
              rng: Callable[[], float] = _random.random) -> str | None:
        angle = ANGLES[int(rng() * len(ANGLES))]
        raw = self._chat(build_messages(c, reason, facts, recent, angle))
        line = clean(raw) if raw is not None else None
        if line and any(similar(line, r) for r in (recent or [])[-8:]):
            return None                          # the model repeated itself: keep the canned line
        return line

    def soul(self, bones) -> tuple[str, str] | None:
        """Propose a (name, personality) for a buddy from its species, rarity and stats only."""
        s = ", ".join(f"{k} {v}" for k, v in bones.stats.items())
        raw = self._chat([
            {"role": "system", "content": (
                "You name and characterise small terminal companion creatures. Reply with exactly two lines:\n"
                "NAME: <1-14 characters, one word, no punctuation>\n"
                "PERSONALITY: <one or two sentences, max 300 characters, second person is not allowed>")},
            {"role": "user", "content": f"A {bones.rarity} {bones.species}. Stats (0-100): {s}."}])
        if not raw:
            return None
        name = re.search(r"^NAME:\s*(.+)$", raw, re.M)
        pers = re.search(r"^PERSONALITY:\s*(.+)$", raw, re.M | re.S)
        if not (name and pers):
            return None
        n = re.sub(r"[^\w-]", "", name.group(1).strip())[:14]
        p = re.sub(r"\s+", " ", pers.group(1)).strip()[:300]
        return (n, p) if n and len(p) >= 10 else None
