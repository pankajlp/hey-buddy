"""
Orchestrator: sends each request to the right agent.
In auto mode the router picks a specialist; if the specialist hands the request back,
the general agent takes it. Manual modes (talk / agent / coder) skip the router.
"""
import logging
import re
import time
from typing import Any, Callable, Dict, Optional

from src.agents.action_agent import ActionAgent
from src.agents.base_agent import BaseAgent
from src.agents.buddy_agent import BuddyAgent
from src.agents.coder_agent import CoderAgent
from src.agents import memory
from src.agents.router import Router
from src.system.mac_bridge import MacBridge
from src.agents.specialists import SPECIALISTS, SpecialistAgent

logger = logging.getLogger("heybuddy.orchestrator")

LABELS = {"talk": "💬 Talk", "coder": "💻 Coder", "general": "🧠 General"} | {
    k: s.label for k, s in SPECIALISTS.items()}

# Manual modes chosen in the UI or CLI
MANUAL_MODES = {"buddy": "talk", "talk": "talk", "agent": "general", "coder": "coder"}

BARE_CONFIRMATION = re.compile(
    r"^\s*(yes|yeah|yep|ok(ay)?|sure|go ahead|do it|proceed|continue|haan|theek hai|kar do)[\s.!,]*(please)?[\s.!]*$", re.I)

ORDINALS = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4, "fifth": 5, "5th": 5, "five": 5, "top": 1}
PLAY_NTH_VIDEO = re.compile(
    r"^\s*(ok(ay)?,?\s+|now\s+|no,?\s+|just\s+)?(play|open|watch|start)\s+(the\s+)?(?P<n>first|second|third|fourth|fifth|top|1st|2nd|3rd|4th|5th|\d+(st|nd|rd|th)?|one|two|three|four|five)"
    r"(\s+(one|video|result|song))?(\s+instead)?[\s.!]*$", re.I)
PLAY_ON_YOUTUBE = re.compile(r"^\s*(play|watch|put on)\s+(?P<q>.+?)\s+(on|in|from)\s+youtube[\s.!]*$", re.I)


def _spoken(result: str) -> str:
    """A short spoken reply from a skill result."""
    if result.startswith("Playing video"):
        return result.split(". Page:")[0].replace("Playing video #1: ", "Playing ").replace("Playing video #", "Playing number ") + "."
    return result.split(". Page:")[0]


# (agent key, label, how it was chosen)
RouteCallback = Callable[[str, str, str], None]


class Orchestrator:
    def __init__(self, on_route: Optional[RouteCallback] = None, tool_filter: Optional[Callable[[BaseAgent], None]] = None,
                 **agent_callbacks: Any):
        self.router = Router()
        self.on_route = on_route
        self.agents: Dict[str, BaseAgent] = {
            "talk": BuddyAgent(**agent_callbacks),
            "general": ActionAgent(**agent_callbacks),
            "coder": CoderAgent(**agent_callbacks),
        }
        for key, spec in SPECIALISTS.items():
            self.agents[key] = SpecialistAgent(spec, **agent_callbacks)
        if tool_filter:
            for agent in self.agents.values():
                tool_filter(agent)


    def _announce(self, key: str, method: str) -> None:
        logger.info(f"route -> {key} ({method})")
        if self.on_route:
            self.on_route(key, LABELS[key], method)

    @staticmethod
    def _context() -> Optional[str]:
        """Recent conversation (shared by all agents and pages) plus what's on screen right now."""
        parts = []
        history = memory.render()
        if history:
            parts.append(history)
        try:
            front = MacBridge.front_window()
            title = f" – '{front['title']}'" if front.get("title") else ""
            parts.append(f"Now in front: {front['app']}{title}")
        except Exception:
            pass
        return "\n".join(parts) or None

    def _fast_path(self, text: str, context: str):
        """Common requests with one obvious action run directly, without the model picking tools."""
        from src.skills.browser import youtube_play
        m = PLAY_NTH_VIDEO.match(text)
        on_youtube = "youtube" in context.split("Now in front:")[-1].lower() or "youtube" in memory.render(2).lower()
        if m and on_youtube:
            which = ORDINALS.get(m.group("n").lower(), None) or int(re.sub(r"\D", "", m.group("n")) or 1)
            result = youtube_play(which=which)
            return "browser", _spoken(result), [f"youtube_play(which={which}) -> {result[:70]}"]
        m = PLAY_ON_YOUTUBE.match(text)
        if m:
            result = youtube_play(query=m.group("q").strip())
            return "browser", _spoken(result), [f"youtube_play({m.group('q')}) -> {result[:70]}"]
        return None

    def handle(self, text: str, mode: str = "auto", speak: bool = True) -> str:
        # Approvals are answered on the page; a lone "go ahead" reaching here has nothing to approve
        if BARE_CONFIRMATION.match(text):
            reply = "There's nothing waiting for a go-ahead. What would you like me to do?"
            memory.add(text, "talk", [], reply)
            return reply
        context = self._context()
        fast = None if mode in MANUAL_MODES else self._fast_path(text, context or "")
        if fast:
            key, reply, did = fast
            self._announce(key, "direct")
            memory.add(text, key, did, reply)
            return reply
        if mode in MANUAL_MODES:
            key, method = MANUAL_MODES[mode], "chosen"
        else:
            started = time.monotonic()
            route = self.router.route(text, memory.last_agent(), context)
            key, method = route.agent, f"{route.method}, {time.monotonic() - started:.1f}s"
        self._announce(key, method)

        reply = self.agents[key].run(text, speak=speak, context=context)
        did = list(self.agents[key].action_log)
        if self.agents[key].handed_off:
            logger.info(f"{key} handed off: {self.agents[key].handed_off}")
            key = "general"
            self._announce(key, "handed off")
            reply = self.agents[key].run(text, speak=speak, context=context)
            did += self.agents[key].action_log

        memory.add(text, key, did, reply)
        return reply
