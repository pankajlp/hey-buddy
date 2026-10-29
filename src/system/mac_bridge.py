import difflib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.parse

import requests
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.types import ScreenContext

logger = logging.getLogger("heybuddy.mac_bridge")

HELPER_BIN = Path(__file__).parent / "buddy_helper"

def _is_original(title: str) -> bool:
    """False for remixes, lofi, synthwave, etc.: any bracketed note other than "(From ...)" or "(feat. ...)"."""
    rest = re.sub(r"[\(\[](from|feat\.?|featuring|ft\.?)\b[^\)\]]*[\)\]]", "", title, flags=re.I)
    return not re.search(r"[\(\[]| - ", rest) and not _VARIANT_WORDS.search(rest)


_VARIANT_WORDS = re.compile(r"\b(remix|mix|lofi|lo-fi|flip|sad|version|instrumental|unplugged|reprise|slowed|reverb|karaoke|cover)\b", re.I)

class MacBridge:
    """
    Native bridge connecting the agent to macOS APIs, accessibility,
    AppleScript, CoreGraphics, and system commands.
    """

    @staticmethod
    def _run_helper(cmd: str, *args: str, timeout: int = 10) -> Dict[str, Any]:
        """Run the native compiled Swift helper binary."""
        if not HELPER_BIN.exists():
            return {"error": f"Helper binary not found at {HELPER_BIN}"}
        try:
            full_cmd = [str(HELPER_BIN), cmd] + list(args)
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            try:
                result = json.loads(proc.stdout.strip())
            except json.JSONDecodeError:
                result = {"output": proc.stdout.strip()}
            if proc.returncode != 0 and "error" not in result:
                result = {"error": proc.stderr.strip() or f"Helper exited with code {proc.returncode}"}
            return result
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def get_active_app(cls) -> Dict[str, Any]:
        """Get frontmost application name, bundle id, pid, screen dimensions."""
        res = cls._run_helper("app_info")
        if "error" in res or not res.get("app_name"):
            # Fallback to AppleScript
            name = cls.run_applescript('tell application "System Events" to get name of first application process whose frontmost is true')
            return {"app_name": name.strip() if name else "Unknown", "bundle_id": "", "pid": 0}
        return res

    @classmethod
    def get_running_apps(cls) -> List[str]:
        """List active GUI applications."""
        res = cls._run_helper("running_apps")
        if "apps" in res:
            return [app["name"] for app in res["apps"] if app.get("name")]
        return []

    @classmethod
    def get_active_window_title(cls) -> str:
        """Attempt to get window title of active application."""
        script = '''
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            try
                return name of front window of frontApp
            on error
                return ""
            end try
        end tell
        '''
        res = cls.run_applescript(script)
        return res.strip() if res else ""

    @classmethod
    def get_screen_context(cls) -> ScreenContext:
        """Compile a complete situational awareness snapshot of the Mac."""
        app_info = cls.get_active_app()
        window_title = cls.get_active_window_title()
        running = cls.get_running_apps()
        
        return ScreenContext(
            active_app=app_info.get("app_name", "Unknown"),
            bundle_id=app_info.get("bundle_id", ""),
            pid=app_info.get("pid", 0),
            screen_width=int(app_info.get("screen_width", 1920)),
            screen_height=int(app_info.get("screen_height", 1080)),
            mouse_x=int(app_info.get("mouse_x", 0)),
            mouse_y=int(app_info.get("mouse_y", 0)),
            active_window=window_title,
            running_apps=running[:15],
        )

    @classmethod
    def mouse_move(cls, x: int, y: int) -> Dict[str, Any]:
        """Move mouse cursor to (x, y)."""
        return cls._run_helper("mouse_move", str(x), str(y))

    @classmethod
    def mouse_click(cls, x: int, y: int, click_type: str = "left") -> Dict[str, Any]:
        """Click mouse at (x, y) with click_type ('left', 'right', 'double')."""
        return cls._run_helper("mouse_click", str(x), str(y), click_type)

    @classmethod
    def type_text(cls, text: str) -> str:
        """Type text on keyboard."""
        # Sanitize for AppleScript
        escaped = text.replace('\\', '\\\\').replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        return cls.run_applescript(script)

    @classmethod
    def press_hotkey(cls, key: str, modifiers: Optional[List[str]] = None) -> str:
        """
        Press a hotkey (e.g. key='c', modifiers=['command']).
        Modifiers can include 'command', 'option', 'control', 'shift'.
        """
        mod_clause = ""
        if modifiers:
            mod_str = ", ".join(f"{m} down" for m in modifiers)
            mod_clause = f" using {{{mod_str}}}"

        # Special named keys
        special_keys = {
            "enter": "key code 36",
            "return": "key code 36",
            "tab": "key code 48",
            "space": "key code 49",
            "escape": "key code 53",
            "esc": "key code 53",
            "backspace": "key code 51",
            "delete": "key code 51",
            "left": "key code 123",
            "right": "key code 124",
            "down": "key code 125",
            "up": "key code 126",
        }

        key_lower = key.lower()
        if key_lower in special_keys:
            stmt = f"{special_keys[key_lower]}{mod_clause}"
        else:
            escaped_key = key.replace('\\', '\\\\').replace('"', '\\"')
            stmt = f'keystroke "{escaped_key}"{mod_clause}'

        script = f'tell application "System Events" to {stmt}'
        return cls.run_applescript(script)

    @classmethod
    def run_applescript(cls, script: str) -> str:
        """Execute AppleScript code."""
        try:
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
            if proc.returncode == 0:
                return proc.stdout.strip()
            return f"AppleScript Error: {proc.stderr.strip()}"
        except Exception as e:
            return f"Error executing AppleScript: {e}"

    @classmethod
    def run_shell(cls, command: str, cwd: Optional[str] = None) -> str:
        """Execute a shell command on macOS."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd or os.path.expanduser("~"),
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = proc.stdout
            if proc.stderr:
                output += ("\n[stderr]\n" + proc.stderr if output else proc.stderr)
            return output.strip() if output else "(No output, exited with code 0)"
        except subprocess.TimeoutExpired:
            return "Command timed out after 30 seconds."
        except Exception as e:
            return f"Shell execution error: {e}"

    @classmethod
    def spotlight_search(cls, query: str, max_results: int = 10) -> List[str]:
        """Search files/apps using macOS Spotlight metadata index."""
        try:
            cmd = ["mdfind", query]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if proc.returncode == 0:
                lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                return lines[:max_results]
        except Exception as e:
            logger.warning(f"Spotlight search error: {e}")
        return []

    @classmethod
    def open_target(cls, target: str) -> str:
        """Open a file, application, or URL using macOS open."""
        try:
            proc = subprocess.run(["open", target], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                return f"Successfully opened {target}"
            return f"Failed to open {target}: {proc.stderr.strip()}"
        except Exception as e:
            return f"Error opening {target}: {e}"

    @classmethod
    def ocr_image(cls, image_path: str) -> Dict[str, Any]:
        """Run native Apple Vision OCR on an image."""
        return cls._run_helper("ocr_image", image_path, timeout=45)

    # What the last read_screen saw, so clicks can be checked against real text positions
    _last_read: Dict[str, Any] = {"time": 0.0, "points": []}

    @classmethod
    def front_window(cls) -> Dict[str, Any]:
        """Frontmost app and its front window's frame in screen points (no frame if it has no window)."""
        out = cls.run_applescript("""
            tell application "System Events"
                set p to first application process whose frontmost is true
                set appName to name of p
                try
                    set w to front window of p
                    set {x, y} to position of w
                    set {ww, hh} to size of w
                    return appName & "|" & x & "|" & y & "|" & ww & "|" & hh & "|" & (name of w)
                on error
                    return appName
                end try
            end tell""")
        parts = out.split("|", 5)
        info: Dict[str, Any] = {"app": parts[0]}
        if len(parts) == 6 and all(p.lstrip("-").isdigit() for p in parts[1:5]):
            info.update(x=int(parts[1]), y=int(parts[2]), width=int(parts[3]), height=int(parts[4]), title=parts[5])
        return info

    @classmethod
    def read_screen(cls, query: Optional[str] = None, max_items: int = 150,
                    whole_screen: bool = False) -> Dict[str, Any]:
        """
        Screenshot the front window (or the whole main display) and OCR it. Returns text elements
        with click coordinates in screen points (the same space mouse_action uses).
        """
        window = cls.front_window()
        region = None
        if not whole_screen and "width" in window and window["width"] > 50 and window["height"] > 50:
            region = (window["x"], window["y"], window["width"], window["height"])

        fd, path = tempfile.mkstemp(suffix=".png", prefix="buddy_screen_")
        os.close(fd)
        try:
            cmd = ["screencapture", "-x", "-t", "png"]
            cmd += ["-R", ",".join(str(v) for v in region)] if region else ["-m"]
            proc = subprocess.run(cmd + [path], capture_output=True, text=True, timeout=10)
            if proc.returncode != 0 or os.path.getsize(path) == 0:
                return {"error": (
                    "Could not capture the screen. Grant Screen Recording permission to the app "
                    "that runs Hey Buddy (System Settings > Privacy & Security > Screen & System "
                    f"Audio Recording), then restart it. Details: {proc.stderr.strip()}"
                )}
            result = cls.ocr_screen_image(path, query=query, max_items=max_items, region=region)
            if "error" not in result:
                result["app"] = window.get("app", "")
                result["window_title"] = window.get("title", "")
                result["scope"] = "front window" if region else "whole screen"
            return result
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @classmethod
    def ocr_screen_image(cls, path: str, query: Optional[str] = None, max_items: int = 150,
                         region: Optional[tuple] = None) -> Dict[str, Any]:
        """OCR a screenshot and convert pixel positions to screen points (offset by the region, if any)."""
        # The first OCR after boot loads Apple's text model (~20s); later runs take well under a second
        ocr = cls._run_helper("ocr_image", path, timeout=45)
        if "error" in ocr:
            return ocr

        # Retina screenshots are in pixels; clicks are in points
        if region:
            offset_x, offset_y, width_pts = region[0], region[1], region[2]
        else:
            offset_x, offset_y = 0, 0
            width_pts = cls.get_active_app().get("screen_width") or ocr["image_width"]
        scale = ocr["image_width"] / float(width_pts)

        elements = []
        for el in ocr.get("elements", []):
            if el.get("confidence", 0) < 0.3:
                continue
            if query and query.lower() not in el["text"].lower():
                continue
            elements.append({
                "text": el["text"],
                "x": offset_x + round(el["center_x"] / scale),
                "y": offset_y + round(el["center_y"] / scale),
            })

        # Reading order: top to bottom, then left to right (rows ~10pt tall)
        elements.sort(key=lambda e: (e["y"] // 10, e["x"]))
        cls._last_read = {"time": time.time(), "points": [(e["x"], e["y"]) for e in elements]}
        return {
            "screen_size": f"{round(ocr['image_width'] / scale)}x{round(ocr['image_height'] / scale)}",
            "total_found": len(elements),
            "elements": elements[:max_items],
        }

    @classmethod
    def near_recent_text(cls, x: int, y: int, radius: int = 60, max_age: float = 120) -> bool:
        """True if (x, y) is close to text found by a read_screen in the last couple of minutes."""
        if time.time() - cls._last_read["time"] > max_age:
            return False
        return any(abs(px - x) <= radius and abs(py - y) <= radius / 2 for px, py in cls._last_read["points"])

    @staticmethod
    def _applescript_string(text: str) -> str:
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    @staticmethod
    def _store_country() -> str:
        """Two-letter Apple Music storefront from the Mac's region, e.g. 'in'."""
        out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True).stdout
        match = re.search(r"_([A-Z]{2})", out)
        return match.group(1).lower() if match else "us"

    @staticmethod
    def _same_title(a: str, b: str) -> bool:
        norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", s.lower()))
        a, b = norm(a), norm(b)
        return bool(a) and bool(b) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))

    @staticmethod
    def _title_similarity(heard: str, title: str) -> float:
        """How alike a misheard name and a real title are (0-1), ignoring "(From ...)" parts."""
        norm = lambda t: " ".join(re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)|\[.*?\]", "", t.lower())))
        return difflib.SequenceMatcher(None, norm(heard), norm(title)).ratio()

    @classmethod
    def _search_catalog(cls, query: str, country: str):
        """
        Search the Apple Music catalog. Speech recognition often mangles song names
        ("Channa Mary Air" for "Channa Mereya"), so if the full query finds nothing,
        search shorter prefixes and accept only a title that sounds like what was heard.
        """
        def search(term):
            resp = requests.get("https://itunes.apple.com/search", timeout=8, params={
                "term": term, "entity": "song", "limit": 15, "country": country})
            return resp.json().get("results", [])

        songs = search(query)
        if songs:
            # Apple's first hit is sometimes a remix or lofi version; prefer an original
            originals = [s for s in songs[:8] if _is_original(s.get("trackName", ""))]
            return (originals or songs)[0], query

        words = query.split()
        best, best_score = None, 0.0
        for n in range(len(words) - 1, 0, -1):
            term = " ".join(words[:n])
            if len(term) < 4:
                break
            for song in search(term):
                title = song.get("trackName", "")
                score = cls._title_similarity(query, title)
                # Prefer the original over remixes, lofi, sad or instrumental versions
                if not _is_original(title):
                    score -= 0.05
                if score > best_score:
                    best, best_score = song, score
        # 0.62 was tuned on real mishearings: "Channa Mary Air" -> "Channa Mereya" scores 0.64,
        # while unrelated songs sharing a first word ("Channa Ve", "Sunra") score 0.59 or less
        if best and best_score >= 0.62:
            return best, query
        return None, query

    @classmethod
    def play_music(cls, query: str) -> str:
        """Play a song from the user's library, or else from the Apple Music catalog."""
        q = cls._applescript_string(query)
        found = cls.run_applescript(f"""
            tell application "Music"
                set results to (search library playlist 1 for {q} only songs)
                if results is {{}} then return ""
                play item 1 of results
                return (name of item 1 of results) & " by " & (artist of item 1 of results)
            end tell""")
        if found and not found.startswith("AppleScript Error"):
            return f"Playing {found} from your library."

        # Not in the library: find it in the Apple Music catalog
        country = cls._store_country()
        try:
            song, searched = cls._search_catalog(query, country)
        except Exception as e:
            return f"Could not search Apple Music: {e}"
        if not song:
            return (
                f"No song found on Apple Music for '{query}' (it may have been misheard). "
                "Tell the user in one short sentence and ask them to say the song name again."
            )
        title, artist = song["trackName"], song["artistName"]

        # Open the song's album in Music and press the album's Play button
        url = f"music://music.apple.com/{country}/album/{song['collectionId']}?i={song['trackId']}"
        subprocess.run(["open", "-a", "Music", url], capture_output=True, timeout=10)
        volume = cls.run_applescript('tell application "Music" to get sound volume')
        try:
            # Mute while skipping to the right track so earlier tracks aren't heard
            cls.run_applescript('tell application "Music" to set sound volume to 0')
            pressed = cls._run_helper("music_play_album", "15", title, timeout=20)
            if "error" in pressed:
                return f"FAILED: couldn't play '{title}' by {artist}: {pressed['error']}. Tell the user it didn't play."

            for _ in range(int(song.get("trackCount") or 40) + 1):
                time.sleep(0.8)
                current = cls.run_applescript('tell application "Music" to get name of current track')
                if cls._same_title(current, title):
                    return f"Playing {title} by {artist}."
                cls.run_applescript('tell application "Music" to next track')
            cls.run_applescript('tell application "Music" to pause')
            return f"FAILED: couldn't reach '{title}' by {artist} on its album, so playback was stopped. Tell the user it didn't play."
        finally:
            if volume.isdigit():
                cls.run_applescript(f'tell application "Music" to set sound volume to {volume}')
