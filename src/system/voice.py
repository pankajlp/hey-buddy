import logging
import os
import subprocess
import threading
from typing import List, Optional

from src.config import config

logger = logging.getLogger("heybuddy.voice")

class VoiceEngine:
    """macOS native Text-to-Speech and audio feedback engine."""

    @staticmethod
    def play_sound(sound_name: str = "Tink"):
        """Play a classic macOS alert sound (e.g. 'Tink', 'Pop', 'Ping', 'Sosumi')."""
        sound_path = f"/System/Library/Sounds/{sound_name}.aiff"
        if os.path.exists(sound_path):
            try:
                subprocess.Popen(["afplay", sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    @classmethod
    def speak(cls, text: str, async_mode: bool = True):
        """Speak text aloud using macOS 'say' command."""
        if not config.voice_enabled:
            return

        # Clean up text for speech (strip markdown headers, code blocks)
        clean_text = cls._clean_text(text)
        if not clean_text:
            return

        cmd = ["say"]
        if config.voice_name:
            cmd.extend(["-v", config.voice_name])
        if config.voice_rate:
            cmd.extend(["-r", str(config.voice_rate)])
        cmd.append(clean_text)

        if async_mode:
            thread = threading.Thread(target=cls._run_say, args=(cmd,), daemon=True)
            thread.start()
        else:
            cls._run_say(cmd)

    @staticmethod
    def _run_say(cmd: list):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except Exception as e:
            logger.debug(f"Speech error: {e}")

    @staticmethod
    def _clean_text(text: str) -> str:
        """Strip markdown markers and excessive symbols from text for natural speech."""
        lines = []
        in_code_block = False
        for line in text.splitlines():
            line_str = line.strip()
            if line_str.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            # Remove markdown headings, bold, italics
            clean_line = (
                line_str.lstrip("# ")
                .replace("**", "")
                .replace("*", "")
                .replace("`", "")
                .strip()
            )
            if clean_line:
                lines.append(clean_line)
        result = " ".join(lines)
        # Keep speech concise if it's too long
        if len(result) > 400:
            result = result[:397] + "..."
        return result

    @staticmethod
    def list_voices() -> List[str]:
        """List available macOS voices."""
        try:
            res = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5)
            voices = []
            for line in res.stdout.splitlines():
                parts = line.split()
                if parts:
                    voices.append(parts[0])
            return voices
        except Exception:
            return []
