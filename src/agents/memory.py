"""
Conversation memory shared by all agents and all Buddy pages, kept across page reloads and server
restarts. It holds the last few exchanges (what the user said, what Buddy did, what it replied) and
forgets everything after a stretch of silence, so an old conversation doesn't leak into a new one.
"""
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Dict, List

from src.connectors.google_auth import CONFIG_DIR

MEMORY_FILE = CONFIG_DIR / "conversation.json"
MAX_TURNS = 8
FORGET_AFTER_S = 30 * 60  # a new conversation after 30 minutes of silence

_lock = threading.Lock()


def _load() -> List[Dict]:
    try:
        turns = json.loads(MEMORY_FILE.read_text())
        if turns and time.time() - turns[-1]["time"] < FORGET_AFTER_S:
            return turns[-MAX_TURNS:]
    except Exception:
        pass
    return []


def _save(turns: List[Dict]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(turns, ensure_ascii=False))
        os.chmod(MEMORY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def add(user: str, agent: str, did: List[str], reply: str) -> None:
    with _lock:
        turns = _load()
        turns.append({"time": time.time(), "user": user, "agent": agent, "did": did[:6], "reply": reply[:300]})
        _save(turns[-MAX_TURNS:])


def last_agent(within_s: float = 120) -> str:
    """The agent that handled the last request, if it was recent."""
    turns = _load()
    if turns and time.time() - turns[-1]["time"] < within_s:
        return turns[-1]["agent"]
    return ""


def render(max_turns: int = MAX_TURNS) -> str:
    """Recent conversation as text for an agent's prompt ('' if none)."""
    lines = []
    for t in _load()[-max_turns:]:
        ago = int(time.time() - t["time"])
        lines.append(f"User ({ago // 60} min ago)" if ago >= 60 else "User")
        lines[-1] += f": {t['user']}"
        if t["did"]:
            lines.append(f"  Buddy ({t['agent']}) did: " + "; ".join(t["did"]))
        lines.append(f"  Buddy ({t['agent']}) said: {t['reply']}")
    return "\n".join(lines)


def clear() -> None:
    with _lock:
        _save([])
