import logging
import re
from typing import Any, Callable, Dict, List, Optional
import inspect
import time

from src.skills import apps, browser, gmail, linkedin, music, system_settings  # noqa: F401  (registers skills)
from src.skills.registry import SKILLS
from src.system.mac_bridge import MacBridge
from src.system.voice import VoiceEngine

logger = logging.getLogger("heybuddy.tools")

# Tool schema definitions following Ollama / OpenAI function format
TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_screen_context",
            "description": "Inspect the Mac system state: currently active/frontmost application, active window title, screen dimensions, mouse cursor location, and running apps.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": "Read the text in the front window (buttons, menus, labels, messages, page text) with the screen coordinates of each piece of text. Bring the right app to the front first (switch_to_app). Use it before any mouse click and click at a returned x, y. Icons without text are not detected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional: only return text containing this (case-insensitive), e.g. 'Export'. Leave empty to read everything."
                    },
                    "whole_screen": {
                        "type": "boolean",
                        "description": "Read the whole screen instead of just the front window (e.g. the menu bar or several windows)."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "applescript_execute",
            "description": "Execute AppleScript on macOS to automate or query native applications (Finder, Safari, Chrome, Notes, Calendar, Music, DaVinci Resolve, System Events, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The exact AppleScript code to execute."
                    }
                },
                "required": ["script"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shell_execute",
            "description": "Run a bash shell command on macOS to inspect files, execute scripts, check git status, organize folders, or run system utilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash shell command to run."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spotlight_find",
            "description": "Quickly find files, folders, or installed applications across the entire Mac using the native Spotlight index (mdfind).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or file name pattern (e.g., 'report.pdf', 'DaVinci', 'kind:image')."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return (default 10)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app_or_file",
            "description": "Launch an application (e.g. 'Safari', 'DaVinci Resolve', 'Terminal') or open a document / URL with default Mac application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Application name (e.g. 'Notes', 'Safari') or file path or URL."
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_action",
            "description": "Move or click the mouse at screen coordinates. Never guess coordinates: get them from read_screen first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["move", "click"],
                        "description": "Whether to move or click."
                    },
                    "x": {
                        "type": "integer",
                        "description": "Screen X coordinate."
                    },
                    "y": {
                        "type": "integer",
                        "description": "Screen Y coordinate."
                    },
                    "click_type": {
                        "type": "string",
                        "enum": ["left", "right", "double"],
                        "description": "Type of click (default 'left')."
                    }
                },
                "required": ["action", "x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard_action",
            "description": "Type text on the keyboard or trigger keyboard shortcut key combinations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["type", "hotkey"],
                        "description": "'type' to write text, 'hotkey' to press a key combination."
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type (if action is 'type')."
                    },
                    "key": {
                        "type": "string",
                        "description": "Key to press for hotkey (e.g. 'c', 'space', 'enter', 'tab', 'left')."
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Modifier keys (e.g. ['command'], ['command', 'shift'])."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "speak_to_user",
            "description": "Speak aloud directly to the user using macOS speech synthesis voice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The message to speak aloud."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_completed",
            "description": "Mark the task as finished and tell the user the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "The result in one short spoken sentence (under 20 words)."
                    }
                },
                "required": ["summary"]
            }
        }
    }
]

# Tools that change state on the Mac. In safe mode each call needs user confirmation.
SENSITIVE_TOOLS = {
    "applescript_execute",
    "shell_execute",
    "open_app_or_file",
    "mouse_action",
    "keyboard_action",
}

# Actions that always need an explicit click, even inside a voice-approved task:
# they can destroy data, change the system, or send things on the user's behalf.
_HIGH_RISK_SHELL = re.compile(
    r"\b(rm|rmdir|srm|sudo|su|mkfs|diskutil|dd|shutdown|reboot|halt|killall|pkill|kill|"
    r"launchctl|csrutil|tccutil|security|chflags|nvram|systemsetup|pmset|fdesetup)\b"
    r"|\bdefaults\s+(write|delete)\b|\bgit\s+(push|reset\s+--hard|clean)\b"
    r"|\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b|>\s*/(etc|System|usr|Library)/",
    re.IGNORECASE,
)
_HIGH_RISK_APPLESCRIPT = re.compile(
    r"\bdo shell script\b|\bdelete\b|\bempty\s+(the\s+)?trash\b|\bsend\b|\berase\b"
    r"|\bshut down\b|\brestart\b|\blog out\b|\bsystem preferences\b|\bsystem settings\b",
    re.IGNORECASE,
)

# Web page buttons whose effects can't be undone
_HIGH_RISK_PAGE_BUTTON = re.compile(
    r"\b(send|post|publish|delete|remove|buy|pay|purchase|order|checkout|check out|subscribe|unsubscribe|"
    r"submit|confirm|transfer|withdraw|apply|connect|follow|unfollow|accept|decline|sign out|log ?out|share)\b",
    re.IGNORECASE,
)

# Page boxes where pressing Enter sends something to other people
_MESSAGE_FIELD = re.compile(r"\b(message|comment|reply|post|chat|write|compose|text editor)\b", re.IGNORECASE)

# ⌘W closes a tab/window, ⌘Q quits an app, ⌘⌫ deletes files in Finder
_HIGH_RISK_HOTKEYS = {"w", "q", "backspace", "delete"}

def high_risk_reason(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Why this action needs an explicit click, or None if a voice-approved task may run it."""
    if (tool_name == "keyboard_action" and args.get("action") == "hotkey"
            and "command" in [m.lower() for m in args.get("modifiers") or []]
            and str(args.get("key", "")).lower() in _HIGH_RISK_HOTKEYS):
        return "This shortcut closes, quits, or deletes something."
    if (tool_name == "type_on_page" and args.get("submit", True) is not False
            and _MESSAGE_FIELD.search(args.get("field", ""))):
        return "Pressing Enter in a message, comment, or post box sends it."
    if tool_name == "click_on_page" and _HIGH_RISK_PAGE_BUTTON.search(args.get("name", "")):
        return "This button can send, post, delete, or buy something."
    if tool_name == "shell_execute" and _HIGH_RISK_SHELL.search(args.get("command", "")):
        return "This command can delete data or change system settings."
    if tool_name == "applescript_execute" and _HIGH_RISK_APPLESCRIPT.search(args.get("script", "")):
        return "This script can delete, send, or change system settings."
    return None

# Skills that change something important are approved like the general tools above
SENSITIVE_TOOLS |= {name for name, sk in SKILLS.items() if sk.needs_approval}

# Everything: the general tools plus all skills
ALL_TOOLS_SPEC: List[Dict[str, Any]] = TOOLS_SPEC + [sk.spec() for sk in SKILLS.values()]

# Read-only subset for conversational modes that should not act on the Mac.
READ_ONLY_TOOLS_SPEC: List[Dict[str, Any]] = [
    t for t in TOOLS_SPEC if t["function"]["name"] not in SENSITIVE_TOOLS
] + [sk.spec() for sk in SKILLS.values() if sk.read_only]


def tool_specs(names: List[str]) -> List[Dict[str, Any]]:
    """Tool definitions for the given tool/skill names, in that order."""
    by_name = {t["function"]["name"]: t for t in ALL_TOOLS_SPEC}
    return [by_name[n] for n in names]

# Keep tool output from flooding the model's context window.
MAX_TOOL_OUTPUT_CHARS = 8000

# Actions whose result can be checked independently of the text returned by the
# action itself. The observation is returned to the agent before it plans the
# next step, so it can recover when the action did not produce the expected UI.
_PAGE_ACTIONS = {
    "open_url", "web_search", "switch_tab", "close_tab", "click_on_page",
    "type_on_page", "browser_navigate", "new_window", "youtube_play",
    "linkedin_open", "linkedin_search", "linkedin_write",
}
_MUSIC_ACTIONS = {"play_music", "play_playlist", "music_control"}
_APP_ACTIONS = {"open_app", "switch_to_app", "quit_app", "hide_app", "window_action"}
_SCREEN_ACTIONS = {"mouse_action", "keyboard_action", "open_app_or_file"}
_SYSTEM_STATUS_ACTIONS = {"set_volume", "set_dark_mode", "set_wifi"}


def _verify_action(tool_name: str, args: Dict[str, Any], result: str) -> Optional[str]:
    """Return fresh evidence after a state-changing action, when available."""
    if re.search(r"\b(FAILED|REFUSED|BLOCKED|DENIED|Error executing|AppleScript Error|Could not|Couldn't|No .* found)\b",
                 result, re.I):
        return None
    try:
        if tool_name in _PAGE_ACTIONS:
            observation = SKILLS["read_page"].func()
            if observation.startswith("Couldn't read"):
                return f"Page check unavailable: {observation}"
            return f"Page check after action:\n{observation[:2500]}"
        if tool_name in _MUSIC_ACTIONS:
            observation = SKILLS["now_playing"].func()
            return f"Music check after action: {observation}"
        if tool_name in _APP_ACTIONS or tool_name == "open_app_or_file":
            if tool_name == "quit_app":
                observation = SKILLS["list_running_apps"].func()
                return f"App check after quit action: {observation}"
            front = MacBridge.front_window()
            frame = (f"frame is x={front['x']}, y={front['y']}, "
                     f"width={front['width']}, height={front['height']}.") if "width" in front else ""
            return (f"Window check after action: front app is {front.get('app', 'unknown')}; "
                    f"window is {front.get('title', '(untitled)')}; {frame}")
        if tool_name in _SCREEN_ACTIONS:
            screen = MacBridge.read_screen()
            if "error" in screen:
                return f"Screen check unavailable: {screen['error']}"
            lines = [f"{e['text']} ({e['x']}, {e['y']})" for e in screen.get("elements", [])]
            return (f"Screen check after action: {screen.get('app', 'unknown')} — "
                    + ("; ".join(lines[:45]) if lines else "no readable text found."))
        if tool_name in _SYSTEM_STATUS_ACTIONS:
            return f"System check after action: {SKILLS['system_status'].func()}"
        if tool_name in ("shell_execute", "applescript_execute"):
            return ("No generic verification is possible for this custom command. Before continuing, "
                    "use a read-only check suited to its intended effect.")
        if tool_name in SKILLS and not SKILLS[tool_name].read_only:
            return ("No automatic check is configured for this action. Before continuing, use a "
                    "read-only tool suited to its intended effect; don't treat the action's own reply as proof.")
    except Exception as exc:
        logger.warning("Post-action verification failed for %s: %s", tool_name, exc)
        return f"Action ran, but its result could not be independently checked: {exc}"
    return None

def _truncate(output: str) -> str:
    if len(output) <= MAX_TOOL_OUTPUT_CHARS:
        return output
    omitted = len(output) - MAX_TOOL_OUTPUT_CHARS
    return output[:MAX_TOOL_OUTPUT_CHARS] + f"\n... [truncated {omitted} more characters]"

# Dispatcher mapping tool names to actual functions
class ToolDispatcher:
    @staticmethod
    def execute(tool_name: str, args: Dict[str, Any], speak: bool = True) -> str:
        result = ToolDispatcher._execute(tool_name, args, speak)
        check = _verify_action(tool_name, args, result)
        if check:
            result = f"{result}\n\n{check}"
        return _truncate(result)

    @staticmethod
    def _execute(tool_name: str, args: Dict[str, Any], speak: bool) -> str:
        try:
            if tool_name in SKILLS:
                func = SKILLS[tool_name].func
                accepted = inspect.signature(func).parameters
                return func(**{k: v for k, v in args.items() if k in accepted})

            if tool_name == "get_screen_context":
                ctx = MacBridge.get_screen_context()
                return ctx.model_dump_json(indent=2)

            elif tool_name == "read_screen":
                result = MacBridge.read_screen(query=args.get("query") or None,
                                               whole_screen=bool(args.get("whole_screen")))
                if "error" in result:
                    return result["error"]
                where = f"{result['scope']} of {result['app']}" + (
                    f" ('{result['window_title']}')" if result.get("window_title") else "")
                if not result["elements"]:
                    what = f"matching '{args.get('query')}'" if args.get("query") else ""
                    return f"No text found {what} in the {where}.".replace("  ", " ")
                lines = [f"Reading the {where}. Text found ({result['total_found']}):"]
                lines += [f'"{e["text"]}" at ({e["x"]}, {e["y"]})' for e in result["elements"]]
                if result["total_found"] > len(result["elements"]):
                    lines.append("... more text not shown; use the query parameter to narrow it down.")
                return "\n".join(lines)

            elif tool_name == "applescript_execute":
                script = args.get("script", "")
                return MacBridge.run_applescript(script)

            elif tool_name == "shell_execute":
                cmd = args.get("command", "")
                return MacBridge.run_shell(cmd)

            elif tool_name == "spotlight_find":
                query = args.get("query", "")
                max_res = args.get("max_results", 10)
                results = MacBridge.spotlight_search(query, max_res)
                if not results:
                    return f"No results found for query: '{query}'"
                return "\n".join(results)

            elif tool_name == "open_app_or_file":
                target = args.get("target", "")
                return MacBridge.open_target(target)

            elif tool_name == "mouse_action":
                action = args.get("action", "click")
                x = int(args.get("x", 0))
                y = int(args.get("y", 0))
                if action == "move":
                    MacBridge.mouse_move(x, y)
                    return f"Moved mouse to ({x}, {y})"
                if not MacBridge.near_recent_text(x, y):
                    return (f"REFUSED: ({x}, {y}) isn't on any text from a recent read_screen. Bring the right "
                            "app to the front, call read_screen, and click at one of the returned positions.")
                click_type = args.get("click_type", "left")
                MacBridge.mouse_click(x, y, click_type)
                time.sleep(0.3)
                front = MacBridge.front_window()
                return (f"Performed {click_type} click at ({x}, {y}). Front app is now {front['app']}"
                        + (f" ('{front['title']}')." if front.get("title") else ".")
                        + " If that's not the app you meant, stop and switch to the right one.")

            elif tool_name == "keyboard_action":
                action = args.get("action", "type")
                front = MacBridge.front_window()
                # Typing into the Claude app would send a message to the user's Claude chat
                if front["app"].lower() == "claude":
                    return ("REFUSED: the Claude app is in front, so typing would go into the user's Claude chat. "
                            "Switch to the app you meant (switch_to_app) and try again.")
                if action == "type":
                    text = args.get("text", "")
                    MacBridge.type_text(text)
                    return f"Typed '{text}' into {front['app']}."
                else:
                    key = args.get("key", "")
                    mods = args.get("modifiers", [])
                    MacBridge.press_hotkey(key, mods)
                    return f"Pressed {'+'.join(mods + [key])} in {front['app']}."

            elif tool_name == "speak_to_user":
                text = args.get("text", "")
                if not speak:
                    return f"Speech is muted for this request; not spoken: '{text}'"
                VoiceEngine.speak(text)
                return f"Spoke: '{text}'"

            elif tool_name == "task_completed":
                summary = args.get("summary", "Task complete.")
                VoiceEngine.play_sound("Hero")
                return f"TASK_COMPLETED: {summary}"

            else:
                return f"Unknown tool: {tool_name}"

        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            return f"Error executing tool '{tool_name}': {e}"
