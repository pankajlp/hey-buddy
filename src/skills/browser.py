"""
Browser skills for Chrome and Safari. Pages are driven through the browser's accessibility
tree (links and fields by name), not screen coordinates.
"""
import re
import subprocess
import time
import urllib.parse
from typing import Optional

from src.skills.registry import skill
from src.system.mac_bridge import MacBridge

BROWSERS = {"Google Chrome": "com.google.Chrome", "Safari": "com.apple.Safari"}


def _browser() -> str:
    """The browser to use: the one in front, else a running one, else Chrome if installed, else Safari."""
    front = MacBridge.get_active_app().get("app_name", "")
    if front in BROWSERS:
        return front
    running = MacBridge.get_running_apps()
    for name in BROWSERS:
        if name in running:
            return name
    return "Google Chrome" if subprocess.run(["open", "-Ra", "Google Chrome"], capture_output=True).returncode == 0 else "Safari"


def _activate(browser: str) -> None:
    MacBridge.run_applescript(f'tell application "{browser}" to activate')
    time.sleep(0.3)


# Buddy's own page; browser tasks must act on the user's other windows, never on it
HUD_URL_PREFIXES = ("http://127.0.0.1:8765", "http://localhost:8765")


def _front_page_window(browser: str) -> None:
    """If Buddy's own window is the browser's front window, bring the user's latest other window forward."""
    prefixes = " or ".join(f'u starts with "{p}"' for p in HUD_URL_PREFIXES)
    if browser == "Safari":
        url_of = "URL of current tab of w"
    else:
        url_of = "URL of active tab of w"
    MacBridge.run_applescript(f"""
        tell application "{browser}"
            if (count of windows) is 0 then return
            set w to front window
            set u to {url_of}
            if not ({prefixes}) then return
            repeat with w in windows
                set u to {url_of}
                if not ({prefixes}) then
                    set index of w to 1
                    return
                end if
            end repeat
        end tell""")


def _helper(cmd: str, *args: str) -> dict:
    return MacBridge._run_helper(cmd, *args, timeout=20)


def _page_url(browser: str) -> str:
    if browser == "Safari":
        return MacBridge.run_applescript('tell application "Safari" to get URL of current tab of front window')
    return MacBridge.run_applescript('tell application "Google Chrome" to get URL of active tab of front window')


def _page_title(browser: str) -> str:
    _front_page_window(browser)
    if browser == "Safari":
        return MacBridge.run_applescript('tell application "Safari" to get name of current tab of front window')
    return MacBridge.run_applescript('tell application "Google Chrome" to get title of active tab of front window')


@skill(
    "open_url",
    "Open a website in the browser (Chrome or Safari), e.g. 'linkedin.com' or a full URL.",
    {"url": {"type": "string", "description": "Website or URL, e.g. 'youtube.com' or 'https://www.linkedin.com/in/me/'."}},
    ["url"],
)
def open_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    browser = _browser()
    subprocess.run(["open", "-a", browser, url], capture_output=True, timeout=10)
    time.sleep(2.0)
    return f"Opened {url} in {browser}. Page: {_page_title(browser)}."


@skill(
    "web_search",
    "Search the web (Google) or a site's own search, e.g. site='youtube' or 'linkedin'.",
    {
        "query": {"type": "string", "description": "What to search for."},
        "site": {"type": "string", "enum": ["google", "youtube", "linkedin", "amazon", "wikipedia"],
                 "description": "Where to search (default google)."},
    },
    ["query"],
)
def web_search(query: str, site: str = "google") -> str:
    q = urllib.parse.quote_plus(query)
    urls = {
        "google": f"https://www.google.com/search?q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "linkedin": f"https://www.linkedin.com/search/results/all/?keywords={q}",
        "amazon": f"https://www.amazon.in/s?k={q}",
        "wikipedia": f"https://en.wikipedia.org/w/index.php?search={q}",
    }
    return open_url(urls.get(site or "google", urls["google"]))


@skill("list_tabs", "List the open tabs in the front browser window.", read_only=True)
def list_tabs() -> str:
    browser = _browser()
    _front_page_window(browser)
    if browser == "Safari":
        out = MacBridge.run_applescript('tell application "Safari" to get name of every tab of front window')
    else:
        out = MacBridge.run_applescript('tell application "Google Chrome" to get title of every tab of front window')
    if out.startswith("AppleScript Error") or not out:
        return f"No open {browser} window."
    tabs = [t.strip() for t in out.split(", ")]
    return f"{browser} tabs: " + "; ".join(f"{i + 1}. {t}" for i, t in enumerate(tabs))


@skill(
    "switch_tab",
    "Switch to the browser tab whose title contains this text.",
    {"title": {"type": "string", "description": "Part of the tab's title, e.g. 'LinkedIn'."}},
    ["title"],
)
def switch_tab(title: str) -> str:
    browser = _browser()
    _front_page_window(browser)
    q = MacBridge._applescript_string(title)
    if browser == "Safari":
        script = f'''tell application "Safari"
            repeat with t in tabs of front window
                if name of t contains {q} then
                    set current tab of front window to t
                    return name of t
                end if
            end repeat
        end tell'''
    else:
        script = f'''tell application "Google Chrome"
            set i to 0
            repeat with t in tabs of front window
                set i to i + 1
                if title of t contains {q} then
                    set active tab index of front window to i
                    return title of t
                end if
            end repeat
        end tell'''
    found = MacBridge.run_applescript(script)
    if not found or found.startswith("AppleScript Error"):
        return f"No tab matching '{title}'. {list_tabs()}"
    _activate(browser)
    return f"Switched to tab: {found}."


@skill("close_tab", "Close the current browser tab.", needs_approval=True)
def close_tab() -> str:
    browser = _browser()
    _front_page_window(browser)
    title = _page_title(browser)
    if browser == "Safari":
        MacBridge.run_applescript('tell application "Safari" to close current tab of front window')
    else:
        MacBridge.run_applescript('tell application "Google Chrome" to close active tab of front window')
    return f"Closed tab: {title}."


@skill(
    "page_elements",
    "List the links, buttons, and text fields on the current web page, optionally only those containing a word.",
    {"filter": {"type": "string", "description": "Optional word to narrow the list, e.g. 'profile' or 'search'."}},
    read_only=True,
)
def page_elements(filter: Optional[str] = None) -> str:
    browser = _browser()
    _front_page_window(browser)
    res = _helper("web_elements", BROWSERS[browser], *( [filter] if filter else [] ))
    if "error" in res:
        return f"Couldn't read the page: {res['error']}"
    items = res.get("elements", [])
    if not items:
        return f"No links or fields{' matching ' + repr(filter) if filter else ''} on '{res.get('page', '')}'."
    shown = items[:80]
    lines = [f"On '{res.get('page', '')}' ({len(items)} found):"] + [f"- {e['type']}: {e['name']}" for e in shown]
    if len(items) > len(shown):
        lines.append("... more; use filter to narrow down.")
    return "\n".join(lines)


@skill(
    "click_on_page",
    "Click a link or button on the current web page by its name (as listed by page_elements).",
    {"name": {"type": "string", "description": "The link or button name, or a distinctive part of it."}},
    ["name"],
)
def click_on_page(name: str) -> str:
    browser = _browser()
    _front_page_window(browser)
    if "youtube.com/results" in _page_url(browser):
        return "On YouTube results, don't click by name (it often hits ads or channels): use youtube_play with which=N."
    res = _helper("web_press", BROWSERS[browser], name)
    if "error" in res:
        return f"{res['error']}. Use page_elements to see what's on the page."
    time.sleep(1.5)
    return f"Clicked {res['type']} '{res['clicked']}'. Page is now: {_page_title(browser)}."


@skill(
    "type_on_page",
    "Type text into a field on the current web page (e.g. a search box), optionally pressing Enter.",
    {
        "field": {"type": "string", "description": "The field's name or placeholder, e.g. 'Search'."},
        "text": {"type": "string", "description": "What to type."},
        "submit": {"type": "boolean", "description": "Press Enter afterwards (default true)."},
    },
    ["field", "text"],
    needs_approval=True,  # typing + Enter can send messages or submit forms
)
def type_on_page(field: str, text: str, submit: bool = True) -> str:
    browser = _browser()
    _front_page_window(browser)
    _activate(browser)
    res = _helper("web_focus", BROWSERS[browser], field)
    if "error" in res:
        return f"{res['error']}. Use page_elements to see the fields on the page."
    time.sleep(0.3)
    front = MacBridge.get_active_app().get("app_name", "")
    if front != browser:
        return f"Stopped: {front} is in front instead of {browser}, so nothing was typed."
    MacBridge.run_applescript('tell application "System Events" to keystroke "a" using command down')
    MacBridge.type_text(text)
    if submit:
        MacBridge.press_hotkey("enter")
        time.sleep(1.5)
    return f"Typed '{text}' into '{res['focused']}'{' and pressed Enter' if submit else ''}. Page is now: {_page_title(browser)}."


@skill("read_page", "Read the title and text of the current web page.", read_only=True)
def read_page() -> str:
    browser = _browser()
    _front_page_window(browser)
    res = _helper("web_text", BROWSERS[browser])
    if "error" in res:
        return f"Couldn't read the page: {res['error']}"
    return f"Page: {res.get('page', '')}\n{res.get('text', '')[:4000]}"


NAV_KEYS = {
    "back": ("[", ["command"]), "forward": ("]", ["command"]), "reload": ("r", ["command"]),
    "new_tab": ("t", ["command"]), "scroll_down": ("space", []), "scroll_up": ("space", ["shift"]),
}


@skill(
    "browser_navigate",
    "Browser navigation: back, forward, reload, new_tab, scroll_down, scroll_up.",
    {"action": {"type": "string", "enum": list(NAV_KEYS)}},
    ["action"],
)
def browser_navigate(action: str) -> str:
    if action not in NAV_KEYS:
        return f"Unknown action '{action}'."
    browser = _browser()
    _front_page_window(browser)
    _activate(browser)
    key, mods = NAV_KEYS[action]
    MacBridge.press_hotkey(key, mods)
    time.sleep(1.0)
    return f"{action.replace('_', ' ').capitalize()}. Page: {_page_title(browser)}."


@skill(
    "new_window",
    "Open a new browser window, optionally incognito/private, optionally at a website.",
    {
        "incognito": {"type": "boolean", "description": "Incognito (Chrome) / Private (Safari) window."},
        "url": {"type": "string", "description": "Optional website to open in it."},
    },
)
def new_window(incognito: bool = False, url: Optional[str] = None) -> str:
    browser = _browser()
    if browser == "Google Chrome":
        props = ' with properties {mode:"incognito"}' if incognito else ""
        MacBridge.run_applescript(f'tell application "Google Chrome" to make new window{props}')
        if url:
            target = url if re.match(r"^[a-z]+://", url, re.I) else "https://" + url
            MacBridge.run_applescript(
                f'tell application "Google Chrome" to set URL of active tab of front window to {MacBridge._applescript_string(target)}')
    else:
        _activate(browser)
        MacBridge.press_hotkey("n", ["command", "shift"] if incognito else ["command"])
        if url:
            time.sleep(0.8)
            return open_url(url)
    _activate(browser)
    kind = ("incognito" if browser == "Google Chrome" else "private") if incognito else "new"
    return f"Opened a {kind} {browser} window" + (f" at {url}." if url else ".")


@skill(
    "youtube_play",
    "Play a YouTube video: searches YouTube (if a query is given) and opens the Nth real video result, "
    "skipping ads, channels, and playlists. Without a query it uses the YouTube page already open.",
    {
        "query": {"type": "string", "description": "What to search for, e.g. 'lofi study music'. Omit to use the current results page."},
        "which": {"type": "integer", "description": "Which result to play: 1 = first (default), 2 = second, ..."},
    },
)
def youtube_play(query: Optional[str] = None, which: int = 1) -> str:
    browser = _browser()
    if query:
        open_url(f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}")
    _front_page_window(browser)
    videos = []
    for _ in range(6):  # results load progressively
        res = _helper("web_links", BROWSERS[browser], "/watch?v=")
        names: dict = {}  # video id -> best (longest) link text; dicts keep page order
        for link in res.get("links", []):
            vid = urllib.parse.parse_qs(urllib.parse.urlparse(link["url"]).query).get("v", [""])[0]
            if vid and "googleadservices" not in link["url"] and len(link["name"]) > len(names.get(vid, "")):
                names[vid] = link["name"]
        videos = list(names.items())
        if len(videos) >= max(1, which):
            break
        time.sleep(0.7)
    if not videos:
        return "No videos found on this YouTube page."
    which = max(1, min(int(which or 1), len(videos)))
    vid, name = videos[which - 1]
    open_url(f"https://www.youtube.com/watch?v={vid}")
    return f"Playing video #{which}: {name[:90]}. Page: {_page_title(browser)}."
