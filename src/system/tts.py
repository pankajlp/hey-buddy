"""
Natural-sounding speech, generated on-device through MLX. Buddy can speak in several voices,
picked from the dropdown on the HUD page (saved as "voice" in settings.json):

  clone:<id>    a cloned voice: Chatterbox Turbo conditioned on voices/<id>/conds.pkl.
                Replies in Hindi (Devanagari) go to multilingual Chatterbox with the same voice
                (voices/<id>/conds_multi.pkl), since Turbo only speaks English.
  kokoro:<name> Kokoro (82M parameters) with a stock voice, e.g. kokoro:af_heart
  system        the Mac's built-in voices (spoken by the page itself, not here)

Voice profiles live outside the repo in ~/Library/Application Support/HeyBuddy/voices/<id>/
(conds.pkl, optional conds_multi.pkl, optional name.txt with the display name). Any folder
added there shows up in the dropdown. If a cloned voice can't load, Buddy falls back to Kokoro;
if Kokoro fails too, the page uses the Mac's built-in voices.
"""
import io
import logging
import os
import pickle
import re
import threading
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from src import user_settings
from src.config import config
from src.system import hinglish
from src.system.transcriber import MLX_LOCK

logger = logging.getLogger("heybuddy.tts")

KOKORO_MODEL = "mlx-community/Kokoro-82M-bf16"
MULTI_MODEL = "mlx-community/chatterbox-fp16"
SAMPLE_RATE = 24000
MAX_CHARS = 800
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Kokoro's Hindi voices need espeak-ng (brew install espeak-ng). The login service has no Homebrew
# on its PATH, so point the phonemizer at the library directly when it is there.
for _lib in ("/opt/homebrew/lib/libespeak-ng.dylib", "/usr/local/lib/libespeak-ng.dylib"):
    if os.path.exists(_lib):
        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", _lib)
        break

# Stock Kokoro voices offered in the dropdown (first letter = accent: a US, b UK).
KOKORO_VOICES = {
    "af_heart": "Heart (US female)",
    "af_bella": "Bella (US female)",
    "am_michael": "Michael (US male)",
    "bf_emma": "Emma (UK female)",
    "bm_george": "George (UK male)",
}

_load_lock = threading.Lock()
_models: Dict[str, object] = {}       # "turbo" | "multi" | "kokoro" -> model
_failed: Dict[str, str] = {}          # model key or voice id -> error text
_conds: Dict[Tuple[str, str], object] = {}   # (clone id, "turbo"|"multi") -> conditionals
_selected: Optional[str] = None       # set from the dropdown; None = settings / default


# ---------------------------------------------------------------- the voice list
def voices_dir() -> Path:
    return Path(config.voices_dir)


def _clones() -> Dict[str, Path]:
    """clone id -> folder holding conds.pkl. Includes the older single-profile location."""
    out: Dict[str, Path] = {}
    d = voices_dir()
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if (p / "conds.pkl").is_file():
                out[p.name] = p
    legacy = Path(config.voice_profile)
    if not out and legacy.is_file():
        out["my-voice"] = legacy.parent
    return out


def _clone_label(cid: str, folder: Path) -> str:
    try:
        name = (folder / "name.txt").read_text().strip()
    except OSError:
        name = ""
    return name or cid.replace("-", " ").title()


def list_voices() -> List[Dict[str, str]]:
    out = [{"id": f"clone:{cid}", "label": f"{_clone_label(cid, p)} (cloned)", "kind": "clone"}
           for cid, p in _clones().items()]
    out += [{"id": f"kokoro:{k}", "label": f"Kokoro – {v}", "kind": "kokoro"} for k, v in KOKORO_VOICES.items()]
    out.append({"id": "system", "label": "Mac system voice", "kind": "system"})
    return out


def _valid(vid: Optional[str]) -> bool:
    if not vid:
        return False
    if vid == "system":
        return True
    kind, _, name = vid.partition(":")
    if kind == "clone":
        return name in _clones()
    if kind == "kokoro":
        return bool(re.fullmatch(r"[a-z]{2}_[a-z]+", name))
    return False


def _default_voice() -> str:
    e = config.tts_engine
    if e == "system":
        return "system"
    clones = _clones()
    if e in ("auto", "clone") and clones:
        return f"clone:{next(iter(clones))}"
    return f"kokoro:{config.kokoro_voice}"


def current_voice() -> str:
    """The voice the user picked (dropdown / settings.json), else the configured default."""
    for vid in (_selected, user_settings.load().get("voice")):
        if _valid(vid):
            return vid
    return _default_voice()


def set_voice(vid: str) -> str:
    global _selected
    if not _valid(vid):
        raise ValueError(f"unknown voice: {vid}")
    _selected = vid
    user_settings.save({"voice": vid})
    _failed.pop(vid, None)     # give a voice that failed before another try
    logger.info("Voice set to %s", vid)
    return vid


def _speaking_voice() -> Optional[str]:
    """The voice that will actually speak here: the chosen one, or Kokoro if a clone failed."""
    vid = current_voice()
    if vid == "system":
        return None
    if vid.startswith("clone:") and (vid in _failed or "turbo" in _failed):
        vid = f"kokoro:{config.kokoro_voice}"
    if vid.startswith("kokoro:") and "kokoro" in _failed:
        return None
    return vid


def enabled() -> bool:
    return _speaking_voice() is not None


def engine() -> Optional[str]:
    v = _speaking_voice()
    return v.split(":")[0] if v else None


# ---------------------------------------------------------------- loading
def _model(key: str):
    with _load_lock:
        if key not in _models and key not in _failed:
            try:
                from mlx_audio.tts.utils import load_model
                repo = {"turbo": config.clone_model, "multi": MULTI_MODEL, "kokoro": KOKORO_MODEL}[key]
                _models[key] = load_model(repo)
            except Exception as ex:
                _failed[key] = str(ex)
                logger.warning("%s voice model unavailable (%s); falling back", key, ex)
        return _models.get(key)


def _clone_conds(cid: str, kind: str):
    """Voice profile of clone `cid` for the "turbo" (English) or "multi" (Hindi etc.) model."""
    key = (cid, kind)
    with _load_lock:
        if key not in _conds:
            folder = _clones().get(cid)
            fname = "conds.pkl" if kind == "turbo" else "conds_multi.pkl"
            try:
                if folder is None or not (folder / fname).is_file():
                    raise FileNotFoundError(f"no {fname} for voice {cid}")
                if kind == "turbo":
                    from mlx_audio.tts.models.chatterbox_turbo import Conditionals
                    _conds[key] = Conditionals.load(folder / fname)
                else:
                    from mlx_audio.tts.models.chatterbox.chatterbox import Conditionals
                    with open(folder / fname, "rb") as f:
                        d = pickle.load(f)
                    _conds[key] = Conditionals(d["t3"], d["gen"])
            except Exception as ex:
                _conds[key] = None
                if kind == "turbo":
                    _failed[f"clone:{cid}"] = str(ex)
                logger.warning("Voice %s (%s) unavailable: %s", cid, kind, ex)
        return _conds[key]


# ---------------------------------------------------------------- speaking
def _hindi_text(text: str) -> str:
    # Drop English asides like "(Yes, I will ...)" that the model adds after a Hindi reply.
    text = re.sub(r"\([^()]*\)", lambda m: m.group(0) if DEVANAGARI.search(m.group(0)) else " ", text)
    return _clone_text(text)


def _clone_text(text: str) -> str:
    # Chatterbox reads symbols literally; keep it to plain spoken text.
    text = re.sub(r"[*_#`~>|]+", " ", text)
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()


def _audio(r) -> np.ndarray:
    return np.asarray(r.audio, dtype=np.float32).reshape(-1)


def _render(vid: str, text: str, speed: float) -> Optional[List[np.ndarray]]:
    """Audio chunks for `text` in voice `vid`, or None if that voice can't speak right now."""
    kind, _, name = vid.partition(":")
    if kind == "clone":
        if DEVANAGARI.search(text):
            model, conds = _model("multi"), _clone_conds(name, "multi")
            if model is not None and conds is not None:
                model._conds = conds
                return [_audio(r) for r in model.generate(text=_hindi_text(text), lang_code="hi")]
        model, conds = _model("turbo"), _clone_conds(name, "turbo")
        if model is None or conds is None:
            return None
        model._conds = conds
        chunks = [_audio(r) for r in model.generate(text=_clone_text(text))]
        if len(chunks) > 1:   # short natural pause between sentences
            gap = np.zeros(int(SAMPLE_RATE * 0.12), dtype=np.float32)
            chunks = [c for ch in chunks for c in (ch, gap)][:-1]
        return chunks
    model = _model("kokoro")
    if model is None:
        return None
    if DEVANAGARI.search(text):   # Hindi: Kokoro's Hindi voice of the same gender
        name = "hm_omega" if name[1:2] == "m" else "hf_alpha"
        text = _hindi_text(text)
    return [_audio(r) for r in model.generate(text=text, voice=name, speed=max(0.7, min(speed, 1.5)),
                                              lang_code=name[0])]


def synthesize(text: str, speed: float = 1.0, voice: Optional[str] = None) -> bytes:
    """WAV bytes (24 kHz mono, 16-bit) of the text in the chosen voice (Kokoro if that fails)."""
    text = text.strip()[:MAX_CHARS]
    vid = voice if _valid(voice) and voice != "system" else _speaking_voice()
    if vid is None:
        raise RuntimeError("speech is set to the Mac system voice")
    if hinglish.looks_hinglish(text):
        # Hindi in Roman letters: rewrite in Devanagari first (Buddy's own chat model, already
        # loaded) so the Hindi voice says it right; read with English sounds it comes out garbled.
        converted = hinglish.to_devanagari(text, config.ollama_host, config.default_buddy_model)
        if converted:
            logger.info("Hinglish spoken as Devanagari: %s", converted[:120])
            text = converted
    with MLX_LOCK:  # MLX shares the GPU with Whisper and Ollama: one job at a time
        chunks = _render(vid, text, speed)
        if chunks is None and vid.startswith("clone:"):
            chunks = _render(f"kokoro:{config.kokoro_voice}", text, speed)
    if chunks is None:
        raise RuntimeError("; ".join(f"{k}: {v}" for k, v in _failed.items()) or "no voice available")
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def warm_up(voice: Optional[str] = None) -> None:
    """Load a voice and run it once, so its first real reply isn't slow."""
    vid = voice or _speaking_voice()
    if not vid or vid == "system":
        return
    started = time.monotonic()
    synthesize("Ready.", voice=vid)
    cid = vid.partition(":")[2]
    if vid.startswith("clone:") and (_clones().get(cid, Path("/nonexistent")) / "conds_multi.pkl").is_file():
        synthesize("ठीक है।", voice=vid)   # load the Hindi voice now, not on the first Hindi reply
    logger.info(f"Voice ready: {vid} in {time.monotonic() - started:.1f}s")
