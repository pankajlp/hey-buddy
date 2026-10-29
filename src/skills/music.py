from src.skills.registry import skill
from src.system.mac_bridge import MacBridge


def _music(script: str) -> str:
    return MacBridge.run_applescript(f'tell application "Music"\n{script}\nend tell')


@skill(
    "play_music",
    "Play a song, artist, or album in the Music app, from the library or the Apple Music catalog.",
    {"query": {"type": "string", "description": "Song and/or artist, e.g. 'Kesariya Arijit Singh'."}},
    ["query"],
)
def play_music(query: str) -> str:
    return MacBridge.play_music(query)


@skill(
    "play_playlist",
    "Play one of the user's playlists in the Music app by name.",
    {"name": {"type": "string", "description": "Playlist name, e.g. 'Favourites'."}},
    ["name"],
)
def play_playlist(name: str) -> str:
    names = _music("get name of every playlist").split(", ")
    match = next((n for n in names if n.lower() == name.lower()), None) \
        or next((n for n in names if name.lower() in n.lower()), None)
    if not match:
        return f"No playlist called '{name}'. Playlists: {', '.join(names[:15])}."
    result = _music(f"play playlist {MacBridge._applescript_string(match)}")
    return f"Playing playlist {match}." if not result.startswith("AppleScript Error") else result


MUSIC_CONTROLS = {
    "pause": "pause",
    "resume": "play",
    "next": "next track\nplay",
    "previous": "previous track\nplay",
    "shuffle_on": "set shuffle enabled to true",
    "shuffle_off": "set shuffle enabled to false",
}


@skill(
    "music_control",
    "Control playback in the Music app: pause, resume, next, previous, shuffle_on, shuffle_off.",
    {"action": {"type": "string", "enum": list(MUSIC_CONTROLS)}},
    ["action"],
)
def music_control(action: str) -> str:
    if action not in MUSIC_CONTROLS:
        return f"Unknown action '{action}'. Use one of: {', '.join(MUSIC_CONTROLS)}."
    result = _music(MUSIC_CONTROLS[action])
    if result.startswith("AppleScript Error"):
        return result
    if action in ("next", "previous", "resume"):
        return now_playing()
    return {"pause": "Paused.", "shuffle_on": "Shuffle on.", "shuffle_off": "Shuffle off."}[action]


@skill("now_playing", "Tell what song is currently playing or paused in the Music app.", read_only=True)
def now_playing() -> str:
    state = _music("get player state as string")
    if state in ("stopped", "") or state.startswith("AppleScript Error"):
        return "Nothing is playing."
    info = _music('get (name of current track) & " by " & (artist of current track)')
    return f"{'Playing' if state == 'playing' else 'Paused'}: {info}."
