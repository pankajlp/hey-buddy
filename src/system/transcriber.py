import logging
import os
import re
import threading
import time
from typing import Any, Dict

import numpy as np

from src.config import env
from src.user_settings import SPEECH_HINTS

logger = logging.getLogger("heybuddy.transcriber")

WHISPER_MODEL = env("WHISPER_MODEL", default="mlx-community/whisper-large-v3-turbo")
WHISPER_LANGUAGE = env("WHISPER_LANG", default="en")
SAMPLE_RATE = 16000

# Primes Whisper with the words it will hear most, so "Hey Buddy" is spelled consistently
INITIAL_PROMPT = (
    "Voice commands for Hey Buddy, a Mac assistant, in English; names may be written in English letters. "
    "Words: Hey Buddy, go ahead, cancel, play, Safari, Finder, Terminal, Downloads"
    + "".join(f", {w}" for w in SPEECH_HINTS) + "."
)

# Whisper tends to invent these on noise or silence
HALLUCINATIONS = {
    "", "you", "thank you", "thank you.", "thanks for watching!", "thanks for watching.",
    "bye.", "bye", ".", "okay.", "so",
}

_lock = threading.Lock()
_warmed = False


def _whisper():
    import mlx_whisper  # imported lazily: loading MLX takes a moment
    return mlx_whisper


def warm_up() -> None:
    """Load the model into memory ahead of the first real request."""
    global _warmed
    if _warmed:
        return
    started = time.monotonic()
    transcribe_pcm16(np.zeros(SAMPLE_RATE // 2, dtype=np.int16).tobytes())
    _warmed = True
    logger.info(f"Whisper ready ({WHISPER_MODEL}) in {time.monotonic() - started:.1f}s")


def transcribe_pcm16(pcm: bytes) -> Dict[str, Any]:
    """Transcribe mono 16 kHz signed 16-bit little-endian PCM audio."""
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if audio.size < SAMPLE_RATE // 4:
        return {"text": "", "seconds": 0.0}

    started = time.monotonic()
    with _lock:  # one transcription at a time; MLX shares the GPU with Ollama
        result = _whisper().transcribe(
            audio,
            path_or_hf_repo=WHISPER_MODEL,
            language=WHISPER_LANGUAGE,
            initial_prompt=INITIAL_PROMPT,
            condition_on_previous_text=False,
            temperature=0.0,
            verbose=None,
        )

    # Drop segments Whisper itself thinks are not speech, or that repeat themselves
    parts = [
        seg.get("text", "").strip()
        for seg in result.get("segments", [])
        if seg.get("no_speech_prob", 0) < 0.6
        and seg.get("avg_logprob", 0) > -1.2
        and seg.get("compression_ratio", 0) < 2.4
    ]
    text = " ".join(p for p in parts if p).strip()
    seconds = round(time.monotonic() - started, 2)
    # English mode: drop words in other scripts/alphabets that Whisper sometimes invents (e.g. "þú")
    text = " ".join(w for w in text.split() if _LATIN_WORD.match(w)).strip()

    if text.lower() in HALLUCINATIONS:
        return {"text": "", "seconds": seconds, "dropped": "filler"}
    # People speak ~2-3 words per second; far more means Whisper made text up
    words = len(text.split())
    if words > 3 and words / (audio.size / SAMPLE_RATE) > 6:
        return {"text": "", "seconds": seconds, "dropped": f"{words} words in {audio.size / SAMPLE_RATE:.1f}s"}
    # Whisper sometimes echoes its priming prompt back instead of transcribing
    if _looks_like_prompt_echo(text):
        return {"text": "", "seconds": seconds, "dropped": "echo of priming prompt"}
    return {"text": text, "seconds": seconds}


_LATIN_WORD = re.compile(r"^[A-Za-z0-9'’&%₹$.,!?:;()\"/+-]+$")


def _normalize(text: str):
    return re.findall(r"[a-z']+", text.lower())


_PROMPT_WORDS = _normalize(INITIAL_PROMPT)


def _looks_like_prompt_echo(text: str) -> bool:
    """True if the text contains a long run of the priming prompt's words."""
    words = _normalize(text)
    run = 8  # longer than any real command's overlap with the word list
    if len(words) < run:
        return False
    prompt_runs = {tuple(_PROMPT_WORDS[i:i + run]) for i in range(len(_PROMPT_WORDS) - run + 1)}
    return any(tuple(words[i:i + run]) in prompt_runs for i in range(len(words) - run + 1))
