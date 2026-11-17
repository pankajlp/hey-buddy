"""
Natural-sounding speech with Kokoro (82M parameters), running on-device through MLX.
Replies are turned into WAV audio here and played by the HUD page. If Kokoro isn't
installed or fails, the page falls back to the Mac's built-in voices.
"""
import io
import logging
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

_model = None
_load_lock = threading.Lock()
_failed: Optional[str] = None


def enabled() -> bool:
    return config.tts_engine == "kokoro" and _failed is None


def _get_model():
    global _model, _failed
    with _load_lock:
        if _model is None and _failed is None:
            try:
                from mlx_audio.tts.utils import load_model
                _model = load_model(KOKORO_MODEL)
            except Exception as e:
                _failed = str(e)
                logger.warning("Kokoro voice unavailable, using the system voice instead: %s", e)
    if _model is None:
        raise RuntimeError(_failed or "Kokoro is not loaded")
    return _model


def synthesize(text: str, speed: float = 1.0) -> bytes:
    """WAV bytes (24 kHz mono, 16-bit) of the text spoken in the configured Kokoro voice."""
    text = text.strip()[:MAX_CHARS]
    voice = config.kokoro_voice
    model = _get_model()
    with MLX_LOCK:  # MLX shares the GPU with Whisper and Ollama: one job at a time
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
    """Load the voice and run it once: the first generation also loads the pronunciation dictionary."""
    if not enabled():
        return
    started = time.monotonic()
    synthesize("Ready.")
    logger.info(f"Kokoro voice ready ({config.kokoro_voice}) in {time.monotonic() - started:.1f}s")
