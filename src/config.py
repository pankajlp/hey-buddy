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
    # Voice of the HUD page: "kokoro" (natural, on-device AI voice) or "system" (the Mac's built-in voices)
    tts_engine: str = env("TTS", default="kokoro").lower()
    # Kokoro voice: af_heart, af_bella, am_michael (US); bf_emma, bm_george (UK); first letter = accent
    kokoro_voice: str = env("KOKORO_VOICE", default="af_heart")
    
    # Behavior & Safety
    auto_screen_context: bool = True
    safe_mode: bool = True  # prompts before destructive shell commands
    max_agent_steps: int = 15
    web_host: str = "127.0.0.1"
    web_port: int = 8765

config = Config()
