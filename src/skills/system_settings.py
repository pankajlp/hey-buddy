import os
import re
import subprocess
import time
from typing import Optional

from src.skills.registry import skill
from src.system.mac_bridge import MacBridge


def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def _volume() -> int:
    out = MacBridge.run_applescript("output volume of (get volume settings)")
    return int(out) if out.isdigit() else -1


@skill(
    "set_volume",
    "Change the Mac's sound volume. Give a level from 0 to 100, or a change: up, down, mute, unmute.",
    {
        "level": {"type": "integer", "description": "Exact volume 0-100."},
        "change": {"type": "string", "enum": ["up", "down", "mute", "unmute"]},
    },
)
def set_volume(level: Optional[int] = None, change: Optional[str] = None) -> str:
    if change == "mute":
        MacBridge.run_applescript("set volume with output muted")
        return "Muted."
    if change == "unmute":
        MacBridge.run_applescript("set volume without output muted")
        return f"Unmuted. Volume {_volume()}%."
    if level is None:
        if change not in ("up", "down"):
            return "Say a volume level or up/down/mute/unmute."
        level = _volume() + (10 if change == "up" else -10)
    level = max(0, min(100, int(level)))
    MacBridge.run_applescript(f"set volume output volume {level} without output muted")
    return f"Volume {_volume()}%."


@skill(
    "set_brightness",
    "Make the built-in display brighter or dimmer (like pressing the brightness keys).",
    {
        "change": {"type": "string", "enum": ["up", "down"]},
        "steps": {"type": "integer", "description": "How many key presses, 1-16 (default 2)."},
    },
    ["change"],
)
def set_brightness(change: str, steps: int = 2) -> str:
    key = 144 if change == "up" else 145  # brightness up / down key codes
    steps = max(1, min(16, int(steps or 2)))
    result = MacBridge.run_applescript(
        f'tell application "System Events"\nrepeat {steps} times\nkey code {key}\ndelay 0.05\nend repeat\nend tell')
    if result.startswith("AppleScript Error"):
        return result
    return f"Brightness {'up' if change == 'up' else 'down'} {steps} steps (works on built-in and Apple displays)."


@skill(
    "set_dark_mode",
    "Turn dark mode on or off, or toggle it.",
    {"mode": {"type": "string", "enum": ["on", "off", "toggle"]}},
    ["mode"],
)
def set_dark_mode(mode: str) -> str:
    value = {"on": "true", "off": "false", "toggle": "not dark mode"}.get(mode)
    if not value:
        return "Use on, off, or toggle."
    result = MacBridge.run_applescript(
        f'tell application "System Events" to tell appearance preferences\n'
        f'set dark mode to {value}\nreturn dark mode\nend tell')
    if result.startswith("AppleScript Error"):
        return result
    return f"Dark mode {'on' if result == 'true' else 'off'}."


def _wifi_device() -> Optional[str]:
    out = _run(["networksetup", "-listallhardwareports"]).stdout
    match = re.search(r"Hardware Port: Wi-Fi\nDevice: (\S+)", out)
    return match.group(1) if match else None


@skill(
    "set_wifi",
    "Turn Wi-Fi on or off.",
    {"power": {"type": "string", "enum": ["on", "off"]}},
    ["power"],
    needs_approval=True,  # turning it off disconnects the Mac
)
def set_wifi(power: str) -> str:
    device = _wifi_device()
    if not device:
        return "This Mac has no Wi-Fi hardware port."
    proc = _run(["networksetup", "-setairportpower", device, power])
    if proc.returncode != 0:
        return f"Could not turn Wi-Fi {power}: {proc.stderr.strip() or proc.stdout.strip()}"
    return f"Wi-Fi {power}."


@skill("system_status", "Report battery, volume, dark mode, and Wi-Fi status.", read_only=True)
def system_status() -> str:
    batt = _run(["pmset", "-g", "batt"]).stdout
    pct = re.search(r"(\d+)%", batt)
    source = "charging" if "AC Power" in batt else "on battery"
    battery = f"Battery {pct.group(1)}% ({source})" if pct else "No battery (desktop Mac)"
    dark = MacBridge.run_applescript('tell application "System Events" to tell appearance preferences to get dark mode')
    muted = MacBridge.run_applescript("output muted of (get volume settings)") == "true"
    device = _wifi_device()
    wifi = _run(["networksetup", "-getairportpower", device]).stdout.strip().split(": ")[-1] if device else "none"
    return (f"{battery}. Volume {_volume()}%{' (muted)' if muted else ''}. "
            f"Dark mode {'on' if dark == 'true' else 'off'}. Wi-Fi {wifi.lower()}.")


@skill("sleep_display", "Turn the display off now (the Mac keeps running).")
def sleep_display() -> str:
    _run(["pmset", "displaysleepnow"])
    return "Display off."


@skill("take_screenshot", "Save a screenshot of the whole screen to the Desktop.")
def take_screenshot() -> str:
    path = os.path.expanduser(time.strftime("~/Desktop/Screenshot %Y-%m-%d at %H.%M.%S.png"))
    proc = _run(["screencapture", "-x", path])
    return f"Saved screenshot to {path}." if proc.returncode == 0 else f"Screenshot failed: {proc.stderr.strip()}"
