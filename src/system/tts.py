"""
Natural-sounding speech, generated on-device through MLX. Two engines:

  clone   your own voice: Chatterbox Turbo conditioned on a saved voice profile (conds.pkl)
  kokoro  Kokoro (82M parameters) with a stock voice

Replies are turned into WAV audio here and played by the HUD page. If the cloned voice can't
load, Buddy falls back to Kokoro; if Kokoro fails too, the page uses the Mac's built-in voices.
"""
import io
import logging
import os
import re
import threading
import time
import wave
from typing import Optional

import numpy as np

from src.config import config
from src.system.transcriber import MLX_LOCK

logger = logging.getLogger("heybuddy.tts")

KOKORO_MODEL = "mlx-community/Kokoro-82M-bf16"
SAMPLE_RATE = 24000
MAX_CHARS = 800

_load_lock = threading.Lock()
_models: dict = {}            # engine -> loaded model
_failed: dict = {}            # engine -> error text
_clone_conds = None


def _wanted() -> str:
    e = config.tts_engine
    if e == "auto":
        return "clone" if os.path.exists(config.voice_profile) else "kokoro"
    return e


def engine() -> Optional[str]:
    """The engine that will actually speak: the wanted one, or Kokoro if the clone failed."""
    e = _wanted()
    if e == "clone" and "clone" in _failed:
        e = "kokoro"
    if e not in ("clone", "kokoro") or e in _failed:
        return None
    return e


def enabled() -> bool:
    return engine() is not None


def _load(e: str):
    global _clone_conds
    with _load_lock:
        if e in _models or e in _failed:
            return _models.get(e)
        try:
            from mlx_audio.tts.utils import load_model
            if e == "clone":
                from mlx_audio.tts.models.chatterbox_turbo import Conditionals
                if not os.path.exists(config.voice_profile):
                    raise FileNotFoundError(f"no voice profile at {config.voice_profile}")
                _clone_conds = Conditionals.load(config.voice_profile)
                _models[e] = load_model(config.clone_model)
            else:
                _models[e] = load_model(KOKORO_MODEL)
        except Exception as ex:
            _failed[e] = str(ex)
            logger.warning("%s voice unavailable (%s); falling back", e, ex)
        return _models.get(e)


def _get_model():
    for _ in range(2):          # second pass picks up the Kokoro fallback after a clone failure
        e = engine()
        if e is None:
            break
        m = _load(e)
        if m is not None:
            return e, m
    raise RuntimeError("; ".join(f"{k}: {v}" for k, v in _failed.items()) or "no voice engine")


def _clone_text(text: str) -> str:
    # Chatterbox reads symbols literally; keep it to plain spoken text.
    text = re.sub(r"[*_#`~>|]+", " ", text)
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()


def synthesize(text: str, speed: float = 1.0) -> bytes:
    """WAV bytes (24 kHz mono, 16-bit) of the text, in the cloned voice or the Kokoro voice."""
    text = text.strip()[:MAX_CHARS]
    e, model = _get_model()
    with MLX_LOCK:  # MLX shares the GPU with Whisper and Ollama: one job at a time
        if e == "clone":
            model._conds = _clone_conds
            chunks = [np.asarray(r.audio, dtype=np.float32).reshape(-1)
                      for r in model.generate(text=_clone_text(text))]
            if len(chunks) > 1:   # short natural pause between sentences
                gap = np.zeros(int(SAMPLE_RATE * 0.12), dtype=np.float32)
                chunks = [c for ch in chunks for c in (ch, gap)][:-1]
        else:
            voice = config.kokoro_voice
            chunks = [np.asarray(r.audio, dtype=np.float32).reshape(-1)
                      for r in model.generate(text=text, voice=voice, speed=max(0.7, min(speed, 1.5)),
                                              lang_code=voice[0])]
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def warm_up() -> None:
    """Load the voice and run it once, so the first real reply isn't slow."""
    if not enabled():
        return
    started = time.monotonic()
    synthesize("Ready.")
    e = engine()
    name = "your cloned voice" if e == "clone" else f"Kokoro ({config.kokoro_voice})"
    logger.info(f"Voice ready: {name} in {time.monotonic() - started:.1f}s")
