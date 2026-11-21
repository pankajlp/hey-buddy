"""
Personal settings (your name, email sign-off, speech hints). They live outside the project in
~/Library/Application Support/HeyBuddy/settings.json, so they never end up in the code or on GitHub.
settings.example.json in the project shows the fields; missing fields fall back to these defaults.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

SETTINGS_FILE = Path.home() / "Library" / "Application Support" / "HeyBuddy" / "settings.json"

DEFAULTS: Dict[str, Any] = {
    "user_name": "the user",  # e.g. "Jane Doe": used when Buddy writes emails and messages for you
    "signoff": "",            # e.g. "Jane": how emails and messages are signed
    "speech_hints": [],       # words Whisper should spell right: names, songs, apps you use often
    "voice": "",              # Buddy's voice, chosen in the HUD dropdown: clone:<id>, kokoro:<name> or system
}


def load() -> Dict[str, Any]:
    try:
        return DEFAULTS | json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return dict(DEFAULTS)


def save(changes: Dict[str, Any]) -> None:
    """Merge `changes` into settings.json, keeping every other field as it is."""
    try:
        current = json.loads(SETTINGS_FILE.read_text())
    except Exception:
        current = {}
    current.update(changes)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False))
    tmp.replace(SETTINGS_FILE)


_settings = load()
USER_NAME: str = _settings["user_name"]
SIGNOFF: str = _settings["signoff"] or USER_NAME.split(" ")[0]
SPEECH_HINTS: List[str] = list(_settings["speech_hints"])
