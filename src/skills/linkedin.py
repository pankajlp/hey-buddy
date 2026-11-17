"""
LinkedIn skills. LinkedIn offers no API for personal inboxes or feeds, so these drive the user's
signed-in browser. They only read and draft: nothing here presses Send, Post, Connect, or Follow
(clicking those still goes through click_on_page, which always asks the user first).
"""
import re
import urllib.parse

from src.skills import browser
from src.skills.registry import skill

SECTIONS = {
    "profile": "https://www.linkedin.com/in/me/",
    "feed": "https://www.linkedin.com/feed/",
    "notifications": "https://www.linkedin.com/notifications/",
    "messages": "https://www.linkedin.com/messaging/",
    "network": "https://www.linkedin.com/mynetwork/",
    "invitations": "https://www.linkedin.com/mynetwork/invitation-manager/",
    "jobs": "https://www.linkedin.com/jobs/",
    "saved_jobs": "https://www.linkedin.com/my-items/saved-jobs/",
    "profile_viewers": "https://www.linkedin.com/me/profile-views/",
    "new_post": "https://www.linkedin.com/feed/?shareActive=true",
}


@skill(
    "linkedin_open",
    "Open a LinkedIn page: profile (the user's own), feed, notifications, messages, network, invitations, "
    "jobs, saved_jobs, profile_viewers, or new_post (opens the post composer).",
    {"section": {"type": "string", "enum": list(SECTIONS)}},
    ["section"],
)
def linkedin_open(section: str) -> str:
    if section not in SECTIONS:
        return f"Unknown section '{section}'. Use one of: {', '.join(SECTIONS)}."
    return browser.open_url(SECTIONS[section])


@skill(
    "linkedin_search",
    "Search LinkedIn for people, jobs, companies, or everything.",
    {
        "query": {"type": "string", "description": "What to search for, e.g. 'data analyst Pune'."},
        "kind": {"type": "string", "enum": ["people", "jobs", "companies", "all"], "description": "Default all."},
    },
    ["query"],
)
def linkedin_search(query: str, kind: str = "all") -> str:
    q = urllib.parse.quote_plus(query)
    urls = {
        "people": f"https://www.linkedin.com/search/results/people/?keywords={q}",
        "companies": f"https://www.linkedin.com/search/results/companies/?keywords={q}",
        "jobs": f"https://www.linkedin.com/jobs/search/?keywords={q}",
        "all": f"https://www.linkedin.com/search/results/all/?keywords={q}",
    }
    return browser.open_url(urls.get(kind or "all", urls["all"]))


@skill(
    "linkedin_write",
    "Type a draft into a text box on the current LinkedIn page (message box, post composer, comment box). "
    "It never presses Enter or Send: the user reviews and sends it.",
    {
        "field": {"type": "string", "description": "The box's name or placeholder, e.g. 'Write a message', 'Text editor'."},
        "text": {"type": "string", "description": "The draft text, written as the user."},
    },
    ["field", "text"],
    needs_approval=True,
)
def linkedin_write(field: str, text: str) -> str:
    result = browser.type_on_page(field, text, submit=False)
    if result.startswith("Typed"):
        return result + " Not sent: the user will review and send it."
    if result.startswith("No text field") and _sponsored_conversation(browser.read_page()):
        return SPONSORED_NOTE
    return result


# Sponsored InMail is an ad: LinkedIn shows its buttons ("Apply now", "Not interested") but no reply box
SPONSORED_NOTE = ("NOT POSSIBLE: the open conversation is a Sponsored message (an ad). LinkedIn doesn't allow "
                  "replies to these; there is no message box. Tell the user that. The only options are its "
                  "buttons, such as the ad's link or 'Not interested'.")


def _sponsored_conversation(page_text: str) -> bool:
    """True if the open conversation is a Sponsored ad: the page says Sponsored and has no message box
    (the conversation list can also show 'Sponsored' next to an ad while a normal chat is open)."""
    if "messaging" not in page_text[:200].lower() or not re.search(r"^\s*Sponsored\b", page_text, re.M):
        return False
    return "write a message" not in browser.page_elements("message").lower()


# LinkedIn's top bar and side card: counters here ("0 notifications") confuse summaries
_BOILERPLATE = re.compile(
    r"^(\d+ (new )?notifications?|\d+|my network|jobs|messaging|notifications|home|me|for business|"
    r"claim .*premium.*|try premium.*|manage your notifications|view settings|all|my posts|mentions|"
    r"skip to .*|close jump menu)$",
    re.IGNORECASE,
)


@skill(
    "linkedin_read",
    "Read the main content of the current LinkedIn page (notifications, messages, search results, a profile), "
    "without LinkedIn's top bar and counters.",
    read_only=True,
)
def linkedin_read() -> str:
    text = browser.read_page()
    lines = text.splitlines()
    title, body = lines[0], [l for l in lines[1:] if l.strip() and not _BOILERPLATE.match(l.strip())]
    note = f"\nNote: {SPONSORED_NOTE.removeprefix('NOT POSSIBLE: ')}" if _sponsored_conversation(text) else ""
    return f"{title}{note}\nMain content (newest first):\n" + "\n".join(body)[:3500]
