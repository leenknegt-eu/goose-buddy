"""What the buddy remembers: projects, recurring bugs, preferences (port of server/memory.ts, MIT).

Local JSON only, never sent to any model. Error summaries are cut to 200 characters (one error
line, not output), and the whole thing can be switched off with `memory.enabled=false`.
"""

from __future__ import annotations

import re
import time
from typing import Any

from . import state
from .suggestions import simple_hash

EXT_LANG = {"ts": "typescript", "tsx": "typescript", "js": "javascript", "jsx": "javascript", "py": "python",
            "rs": "rust", "go": "go", "java": "java", "rb": "ruby", "php": "php", "c": "c", "cpp": "cpp",
            "cs": "csharp", "swift": "swift", "kt": "kotlin", "scala": "scala", "r": "r", "lua": "lua",
            "pl": "perl", "sh": "shell", "bash": "shell", "zsh": "shell", "fish": "shell", "sql": "sql",
            "html": "html", "css": "css", "scss": "scss", "json": "json", "yaml": "yaml", "yml": "yaml",
            "toml": "toml", "xml": "xml", "md": "markdown"}
FRAMEWORK_FILES = {"package.json": "Node.js", "package-lock.json": "Node.js", "requirements.txt": "Python",
                   "Pipfile": "Python", "pyproject.toml": "Python", "Cargo.toml": "Rust", "Cargo.lock": "Rust",
                   "go.mod": "Go", "go.sum": "Go", "pom.xml": "Java", "build.gradle": "Java", "Gemfile": "Ruby",
                   "Gemfile.lock": "Ruby", "composer.json": "PHP", "Package.swift": "Swift", ".NET": ".NET",
                   "csproj": ".NET"}
ERROR_PATTERNS = [re.compile(p, re.I) for p in (
    r"([A-Z][a-z]+Error: .+)", r"(SyntaxError: .+)", r"(TypeError: .+)", r"(ReferenceError: .+)",
    r"(Error: .+)", r"(panic: .+)", r"(Exception: .+)")]
MAX_BUGS = 500


def enabled() -> bool:
    return bool(state.load_config().get("memory", {}).get("enabled", True))


def _get(name: str) -> dict[str, Any]:
    return state.kv_get(f"memory_{name}", {})


def projects() -> dict[str, Any]:
    return _get("projects")


def bugs() -> dict[str, Any]:
    return _get("bugs")


def preferences() -> dict[str, Any]:
    return _get("preferences")


def update_project(pid: str, now: float | None = None, **updates: Any) -> dict[str, Any]:
    now = time.time() if now is None else now
    with state.locked():
        ps = projects()
        p = ps.get(pid) or {"id": pid, "name": pid, "path": "", "language": [], "noteCount": 0, "tags": []}
        p.update({k: v for k, v in updates.items() if v}, id=pid, lastSeen=now)
        ps[pid] = p
        state.kv_set("memory_projects", ps)
    return p


def add_bug(project: str, signature: str, summary: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    with state.locked():
        bs = bugs()
        found = next((b for b in bs.values() if b["projectId"] == project and b["errorSignature"] == signature), None)
        if found:
            found.update(occurrenceCount=found["occurrenceCount"] + 1, lastSeen=now, summary=summary)
            bug = found
        else:
            bug = {"id": f"{project}-{signature[:8]}", "projectId": project, "errorSignature": signature,
                   "summary": summary, "resolved": False, "occurrenceCount": 1, "lastSeen": now, "firstSeen": now}
            bs[bug["id"]] = bug
            if len(bs) > MAX_BUGS:               # keep the newest
                for k in sorted(bs, key=lambda k: bs[k]["lastSeen"])[:len(bs) - MAX_BUGS]:
                    del bs[k]
        state.kv_set("memory_bugs", bs)
    return bug


def resolve_bug(bug_id: str, now: float | None = None) -> dict[str, Any] | None:
    with state.locked():
        bs = bugs()
        b = bs.get(bug_id)
        if not b:
            return None
        b.update(resolved=True, resolvedAt=time.time() if now is None else now)
        state.kv_set("memory_bugs", bs)
    state.bump("bugs_resolved")
    return b


def set_preference(key: str, value: str, context: str, boost: float = 0.1, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    with state.locked():
        ps = preferences()
        old = ps.get(key)
        if old and old["context"] == context:
            old.update(confidence=min(1.0, old["confidence"] + boost), value=value, lastUpdated=now)
        elif old and old["confidence"] > boost:
            old["lastUpdated"] = now
        else:
            ps[key] = {"key": key, "value": value, "context": context, "confidence": boost, "lastUpdated": now}
        state.kv_set("memory_preferences", ps)
    return ps[key]


def extract_project(text: str) -> str | None:
    m = re.search(r"/([^/\s]+)/(?:src|lib|app|packages|tests?)\b", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"github\.com[:/][\w\-.]+/([^/\s]+)", text, re.I)
    return m.group(1) if m else None


def extract_languages(text: str) -> list[str]:
    return sorted({EXT_LANG[e.lower()] for e in re.findall(r"\.([a-z]+)", text, re.I) if e.lower() in EXT_LANG})


def extract_framework(text: str) -> str | None:
    return next((fw for f, fw in FRAMEWORK_FILES.items() if f in text), None)


def extract_errors(text: str) -> list[tuple[str, str]]:
    out = []
    for pat in ERROR_PATTERNS:
        for m in pat.finditer(text):
            full = m.group(0)
            out.append((simple_hash(full), full[:200]))
    return out


def consolidate(text: str) -> None:
    """Learn from a chunk of conversation: project, languages, framework and recurring errors."""
    if not enabled():
        return
    project = extract_project(text)
    if not project:
        return
    update_project(project, name=project, language=extract_languages(text), framework=extract_framework(text))
    for sig, summary in extract_errors(text):
        add_bug(project, sig, summary)


def query(kind: str = "all") -> dict[str, Any]:
    return {k: v for k, v in (("projects", projects()), ("bugs", bugs()), ("preferences", preferences()))
            if kind in ("all", k)}
