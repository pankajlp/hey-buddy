import difflib
import os
import subprocess
import time
from typing import Dict, List, Optional

from src.skills.registry import skill
from src.system.mac_bridge import MacBridge

APP_DIRS = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
            os.path.expanduser("~/Applications")]

# Spoken names that don't match the app's real name
ALIASES = {
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "code": "Visual Studio Code",
    "chrome": "Google Chrome", "settings": "System Settings", "system preferences": "System Settings",
    "davinci": "DaVinci Resolve", "resolve": "DaVinci Resolve", "app store": "App Store",
    "whatsapp": "WhatsApp", "itunes": "Music", "apple music": "Music",
}

_installed: Dict[str, str] = {}
_installed_at = 0.0


def installed_apps() -> Dict[str, str]:
    """Installed app names (lowercase -> real name), refreshed every 10 minutes."""
    global _installed, _installed_at
    if not _installed or time.time() - _installed_at > 600:
        out = subprocess.run(
            ["mdfind", "kMDItemContentType == 'com.apple.application-bundle'"]
            + [arg for d in APP_DIRS if os.path.isdir(d) for arg in ("-onlyin", d)],
            capture_output=True, text=True, timeout=10).stdout
        names = {os.path.basename(p)[:-4] for p in out.splitlines() if p.endswith(".app")}
        _installed = {n.lower(): n for n in names}
        _installed_at = time.time()
    return _installed


def resolve_app(spoken: str, candidates: Optional[List[str]] = None) -> Optional[str]:
    """Best matching app name for what the user said, or None."""
    apps = {c.lower(): c for c in candidates} if candidates is not None else installed_apps()
    key = spoken.lower().strip().removesuffix(" app").removeprefix("the ").strip()
    if key in ALIASES and ALIASES[key].lower() in apps:
        return apps[ALIASES[key].lower()]
    if key in apps:
        return apps[key]
    starts = [n for n in apps if n.startswith(key)]
    if starts:
        return apps[min(starts, key=len)]
    close = difflib.get_close_matches(key, list(apps), n=1, cutoff=0.75)
    return apps[close[0]] if close else None


def running_apps() -> List[str]:
    return MacBridge.get_running_apps()


@skill(
    "open_app",
    "Open (launch) an installed application, or bring it to the front if it's already running.",
    {"name": {"type": "string", "description": "App name as the user said it, e.g. 'Safari', 'VS Code'."}},
    ["name"],
)
def open_app(name: str) -> str:
    app = resolve_app(name)
    if not app:
        return f"No installed app matches '{name}'."
    proc = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=10)
    return f"Opened {app}." if proc.returncode == 0 else f"Could not open {app}: {proc.stderr.strip()}"


@skill(
    "quit_app",
    "Quit a running application normally (it will still ask to save unsaved work).",
    {"name": {"type": "string", "description": "App name, e.g. 'Safari'."}},
    ["name"],
    needs_approval=True,  # quitting the wrong app loses work; the user confirms which one
)
def quit_app(name: str) -> str:
    app = resolve_app(name, running_apps())
    if not app:
        return f"No running app matches '{name}'."
    if app in BUDDY_BROWSERS and _buddy_window_open(app):
        # Quitting the browser would also close Buddy's own window (and its microphone)
        closed = _close_browser_except_buddy(app)
        return f"Closed {closed} of your {app} windows and tabs. Kept Buddy's own window open so I can still hear you."
    result = MacBridge.run_applescript(f"tell application {MacBridge._applescript_string(app)} to quit")
    return f"Quit {app}." if not result.startswith("AppleScript Error") else result


# Buddy's page runs in one of these browsers; its address starts with this
BUDDY_URL = "http://127.0.0.1:8765"
BUDDY_BROWSERS = {"Google Chrome": ("active tab", "URL"), "Safari": ("current tab", "URL")}


def _buddy_window_open(browser: str) -> bool:
    out = MacBridge.run_applescript(f'tell application "{browser}" to get URL of every tab of every window')
    return BUDDY_URL in out


def _close_browser_except_buddy(browser: str) -> int:
    """Close every window and tab in the browser except Buddy's page. Returns how many were closed."""
    out = MacBridge.run_applescript(f"""
        tell application "{browser}"
            set closedCount to 0
            repeat with i from (count of windows) to 1 by -1
                set w to window i
                set hasBuddy to false
                repeat with t in tabs of w
                    if URL of t starts with "{BUDDY_URL}" then set hasBuddy to true
                end repeat
                if hasBuddy then
                    repeat with j from (count of tabs of w) to 1 by -1
                        if URL of tab j of w does not start with "{BUDDY_URL}" then
                            close tab j of w
                            set closedCount to closedCount + 1
                        end if
                    end repeat
                else
                    close w
                    set closedCount to closedCount + 1
                end if
            end repeat
            return closedCount
        end tell""")
    return int(out) if out.strip().isdigit() else 0


@skill(
    "switch_to_app",
    "Bring a running application to the front.",
    {"name": {"type": "string", "description": "App name, e.g. 'Music'."}},
    ["name"],
)
def switch_to_app(name: str) -> str:
    app = resolve_app(name, running_apps()) or resolve_app(name)
    if not app:
        return f"No app matches '{name}'."
    result = MacBridge.run_applescript(f"tell application {MacBridge._applescript_string(app)} to activate")
    return f"Switched to {app}." if not result.startswith("AppleScript Error") else result


@skill(
    "hide_app",
    "Hide a running application's windows (like pressing Command-H).",
    {"name": {"type": "string", "description": "App name. Use 'front' for the app in front."}},
    ["name"],
)
def hide_app(name: str) -> str:
    app = _front_app() if name.lower() in ("front", "this", "current") else resolve_app(name, running_apps())
    if not app:
        return f"No running app matches '{name}'."
    result = MacBridge.run_applescript(
        f'tell application "System Events" to set visible of process {MacBridge._applescript_string(app)} to false')
    return f"Hid {app}." if not result.startswith("AppleScript Error") else result


@skill("list_running_apps", "List the applications that are currently open.", read_only=True)
def list_running_apps() -> str:
    return "Open apps: " + ", ".join(running_apps())


def _front_app() -> str:
    return MacBridge.get_active_app().get("app_name", "")


WINDOW_ACTIONS = ["left_half", "right_half", "maximize", "center", "minimize", "fullscreen"]


@skill(
    "window_action",
    "Arrange a window: left_half, right_half, maximize, center, minimize, or fullscreen. "
    "Acts on the named app's front window, or the frontmost app if no app is given.",
    {
        "action": {"type": "string", "enum": WINDOW_ACTIONS},
        "app": {"type": "string", "description": "App whose window to arrange, e.g. 'Claude'. Omit for the front app."},
    },
    ["action"],
)
def window_action(action: str, app: Optional[str] = None) -> str:
    if app:
        target = resolve_app(app, running_apps())
        if not target:
            return f"No running app matches '{app}'."
        MacBridge.run_applescript(f"tell application {MacBridge._applescript_string(target)} to activate")
        time.sleep(0.4)
    app = _front_app()
    proc = f"process {MacBridge._applescript_string(app)}"
    if action == "minimize":
        script = f'tell application "System Events" to tell {proc} to set value of attribute "AXMinimized" of front window to true'
    elif action == "fullscreen":
        script = f'tell application "System Events" to tell {proc} to set value of attribute "AXFullScreen" of front window to true'
    elif action in ("left_half", "right_half", "maximize", "center"):
        info = MacBridge.get_active_app()
        x, y = int(info.get("visible_x", 0)), int(info.get("visible_y", 25))
        w, h = int(info.get("visible_width", info.get("screen_width", 1440))), int(info.get("visible_height", 850))
        frame = {
            "left_half": (x, y, w // 2, h),
            "right_half": (x + w // 2, y, w - w // 2, h),
            "maximize": (x, y, w, h),
            "center": (x + w // 6, y + h // 8, w * 2 // 3, h * 3 // 4),
        }[action]
        script = (f'tell application "System Events" to tell {proc}\n'
                  f'set position of front window to {{{frame[0]}, {frame[1]}}}\n'
                  f'set size of front window to {{{frame[2]}, {frame[3]}}}\nend tell')
    else:
        return f"Unknown window action '{action}'."
    result = MacBridge.run_applescript(script)
    if result.startswith("AppleScript Error"):
        return f"Couldn't arrange {app}'s window: {result}"
    return f"{app} window: {action.replace('_', ' ')}."
