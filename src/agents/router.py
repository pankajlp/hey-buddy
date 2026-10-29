"""
Router: decides which agent handles a request.
Clear-cut requests are matched by rules (instant); anything else is classified by the model.
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from src.agents.specialists import SPECIALISTS
from src.config import config
from src.core.ollama_client import OllamaClient
from src.skills.apps import ALIASES, installed_apps, resolve_app

logger = logging.getLogger("heybuddy.router")

# Agents beyond the specialists
OTHER_AGENTS = {
    "talk": "Questions and conversation, including how to use apps and their keyboard shortcuts (e.g. 'how do I color grade in Resolve'), "
            "explanations, or describing what's on screen. No actions.",
    "coder": "Writing or debugging code, programming errors, terminal commands, git. Not how-to questions about apps.",
    "general": "Any other task on the Mac: files and folders, documents, typing or clicking in non-browser apps, "
               "and requests that combine several areas.",
}

SYSTEM_RULE = re.compile(
    r"\b(volume|mute|unmute|louder|quieter|softer|brightness|brighter|dimmer|dark mode|light mode|"
    r"wi-?fi|battery|screenshot|screen ?shot)\b"
    r"|\b(sleep|turn off|switch off)\s+(the\s+)?(display|screen|monitor)\b", re.I)
MUSIC_RULE = re.compile(
    r"\b(play|pause|resume|unpause|skip|shuffle|playlist|songs?|music)\b"
    r"|\bwhat('?s| is)\s+(playing|this song)\b|\b(next|previous|last)\s+(song|track)\b", re.I)
BROWSER_RULE = re.compile(
    r"\b(website|web ?page|web ?site|url|browser|tabs?|incognito|private window|youtube|google|wikipedia|amazon|"
    r"instagram|facebook|twitter|github|netflix)\b|\b[\w-]+\.(com|in|org|net|io|co|ai|dev)\b", re.I)
# How-to and knowledge questions ("how do I…", "what's the shortcut for…") are for Talk, unless about code
QUESTION_RULE = re.compile(
    r"^\s*(how (do|can|should) (i|you|we)|how to|what('?s| is| are) the (shortcut|keyboard shortcut|best way|way|difference)|"
    r"what does|why\b|how come|what happened|explain|tell me (about|how|what|why)|what is|who is|when is|where is|"
    r"did you|were you|can you explain|what did you)\b", re.I)
CODE_WORDS = re.compile(
    r"\b(code|coding|python|javascript|typescript|swift|script|function|error|exception|bug|debug|git|terminal|"
    r"bash|zsh|command line|compile|api|regex|sql|json|stack ?trace)\b", re.I)
MAIL_RULE = re.compile(r"\b(e-?mails?|mails?|inbox|gmail|drafts?|unread)\b|\breply to\b", re.I)
MAIL_WORDS = re.compile(r"\b(e-?mails?|mails?|inbox|gmail)\b", re.I)
LINKEDIN_RULE = re.compile(r"\blinked ?in\b", re.I)
NOT_MUSIC = re.compile(r"\b(youtube|video|videos|movie|netflix|podcast|game)\b", re.I)
APP_COMMAND = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+)?(?:open|launch|start|quit|close|exit|switch to|go to|bring up|hide)\s+"
    r"(?:the\s+|my\s+)?(.+?)(?:\s+app(?:lication)?)?\s*[.!?]*\s*$", re.I)
ABOUT_WHAT_HAPPENED = re.compile(r"^\s*(did you|didn'?t you|why|how come|what happened|were you|what did you|have you)\b", re.I)
CLOSE_THIS = re.compile(r"^\s*(please\s+)?(close|shut)\s+(it|this|that)(\s+(page|tab|one|video|site))?\b[\s.!]*$", re.I)
QUIT_WORDS = re.compile(r"\b(close|closed|quit|exit|kill|shut down)\b", re.I)
WINDOW_RULE = re.compile(
    r"\b(window|full ?screen|left half|right half|left side|right side|snap|minimi[sz]e|maximi[sz]e)\b", re.I)


def _names_app(text: str) -> bool:
    """True if the text names an installed app exactly, e.g. 'Chrome', 'Music', 'VS Code'."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    apps = installed_apps()
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            if phrase in ALIASES or (phrase in apps and phrase not in COMMON_WORD_APPS):
                return True
    return False


# Installed apps whose names are everyday words; only count them with the word "app"
COMMON_WORD_APPS = {"photos", "notes", "music", "news", "home", "books", "maps", "mail", "clock", "weather",
                    "stocks", "calendar", "contacts", "reminders", "preview", "podcasts", "tv", "freeform", "passwords"}


@dataclass
class Route:
    agent: str   # specialist key, or talk / coder / general
    method: str  # how it was decided, for logs and the UI


class Router:
    def __init__(self):
        self.client = OllamaClient()

    def route(self, text: str, previous: Optional[str] = None, context: Optional[str] = None) -> Route:
        if previous in ("browser", "linkedin") and CLOSE_THIS.search(text):
            return Route(previous, "rule: follow-up")
        rule = self._by_rules(text)
        if rule:
            return rule
        return self._by_model(text, previous, context)

    def _by_rules(self, text: str) -> Optional[Route]:
        # "open Safari", "quit Music": an installed app name after the verb is unambiguous
        match = APP_COMMAND.match(text)
        if match and len(match.group(1).split()) <= 4 and resolve_app(match.group(1)):
            return Route("apps", "rule")

        # Questions about what Buddy did ("did you close it?", "why didn't…") are answered, not acted on
        if ABOUT_WHAT_HAPPENED.search(text):
            return Route("coder" if CODE_WORDS.search(text) else "talk", "rule: question")

        # "close the music app", "I want Chrome closed": quitting apps is the Apps specialist's job
        if QUIT_WORDS.search(text) and not re.search(r"\b(tabs?|window|page|video)\b", text, re.I) \
                and (re.search(r"\b(apps?|application|browser)\b", text, re.I) or _names_app(text)):
            return Route("apps", "rule")

        areas = set()
        if SYSTEM_RULE.search(text):
            areas.add("system")
        if MUSIC_RULE.search(text) and not NOT_MUSIC.search(text):
            areas.add("music")
        if WINDOW_RULE.search(text):
            areas.add("apps")
        if BROWSER_RULE.search(text):
            areas.discard("music")  # "play a video on YouTube" is a browser task
            areas.add("browser")
        # Mail and LinkedIn have their own specialists, which win over the general browser
        # "mail from LinkedIn" is about Gmail; "draft a LinkedIn post" is about LinkedIn
        if MAIL_WORDS.search(text) or (MAIL_RULE.search(text) and not LINKEDIN_RULE.search(text)):
            areas -= {"browser", "music"}
            areas.add("mail")
        elif LINKEDIN_RULE.search(text):
            areas -= {"browser", "music"}
            areas.add("linkedin")
        if not areas and QUESTION_RULE.search(text):
            return Route("coder" if CODE_WORDS.search(text) else "talk", "rule: question")
        if len(areas) == 1:
            return Route(areas.pop(), "rule")
        if len(areas) > 1:
            return Route("general", "rule: several areas")
        return None

    def _by_model(self, text: str, previous: Optional[str], context: Optional[str] = None) -> Route:
        options = {k: s.description for k, s in SPECIALISTS.items()} | OTHER_AGENTS
        listing = "\n".join(f"- {k}: {d}" for k, d in options.items())
        followup = (f"\nThe previous request went to '{previous}'. Short follow-ups like 'again', "
                    f"'louder', or 'the next one' usually belong there too." if previous else "")
        recent = (f"\nRecent conversation (the request may refer to it; speech recognition can mishear words):\n"
                  f"{context[-1500:]}\n") if context else ""
        prompt = (f"Pick the one assistant that should handle the user's request.\n{listing}{followup}\n{recent}\n"
                  f"Request: {text}\n\nAnswer as JSON: {{\"agent\": \"<name>\"}}")
        try:
            res = self.client.chat(model=config.default_agent_model, temperature=0.0, format="json",
                                   messages=[{"role": "user", "content": prompt}])
            agent = json.loads(res["message"]["content"]).get("agent", "")
            if agent in options:
                return Route(agent, "model")
            logger.warning(f"Router model picked unknown agent {agent!r}")
        except Exception as e:
            logger.warning(f"Router model failed: {e}")
        return Route("general", "fallback")
