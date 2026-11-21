import logging
import os
from typing import Optional

from pydantic import BaseModel


def env(name: str, default: str = "", legacy: Optional[str] = None) -> str:
    """Setting from the environment: BUDDY_<name>, or the older CLICKY_<name> name."""
    return os.getenv(f"BUDDY_{name}") or os.getenv(legacy or f"CLICKY_{name}") or default


def env_int(name: str, default: int) -> int:
    """Whole-number setting; an invalid value falls back to the default instead of crashing startup."""
    raw = env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        logging.getLogger("heybuddy.config").warning("Ignoring BUDDY_%s=%r (not a number); using %s", name, raw, default)
        return default


class Config(BaseModel):
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    
    # Models available in local Ollama
    default_buddy_model: str = env("TALK_MODEL", legacy="CLICKY_BUDDY_MODEL", default="qwen3-coder-32k:latest")
    default_agent_model: str = env("AGENT_MODEL", default="qwen3-coder-32k:latest")
    default_coder_model: str = env("CODER_MODEL", default="qwen3-coder-32k:latest")
    
    # How long Ollama keeps the model in memory after a request (avoids slow reloads)
    ollama_keep_alive: str = env("KEEP_ALIVE", default="30m")

    # Voice (macOS text-to-speech)
    voice_enabled: bool = env("VOICE", default="true").lower() in ("true", "1", "yes")
    voice_name: str = env("VOICE_NAME", default="")  # empty = system default
    voice_rate: int = env_int("VOICE_RATE", 195)
    # Voice of the HUD page: "clone" (your own cloned voice), "kokoro" (natural, on-device AI voice)
    # or "system" (the Mac's built-in voices). "auto" = clone if a voice profile exists, else kokoro.
    tts_engine: str = env("TTS", default="auto").lower()
    # Older single cloned-voice profile, used only when voices_dir has none. Personal data: outside the repo.
    voice_profile: str = env("VOICE_PROFILE", default=os.path.expanduser(
        "~/Library/Application Support/HeyBuddy/voice/conds.pkl"))
    clone_model: str = env("CLONE_MODEL", default="mlx-community/chatterbox-turbo-fp16")
    # Folder of cloned voices: voices/<id>/conds.pkl (+ conds_multi.pkl, name.txt). Shown in the voice dropdown.
    voices_dir: str = env("VOICES_DIR", default=os.path.expanduser(
        "~/Library/Application Support/HeyBuddy/voices"))
    # Kokoro voice: af_heart, af_bella, am_michael (US); bf_emma, bm_george (UK); first letter = accent
    kokoro_voice: str = env("KOKORO_VOICE", default="af_heart")
    
    # Behavior & Safety
    auto_screen_context: bool = True
    safe_mode: bool = True  # prompts before destructive shell commands
    max_agent_steps: int = 15
    web_host: str = "127.0.0.1"
    web_port: int = 8765

config = Config()
