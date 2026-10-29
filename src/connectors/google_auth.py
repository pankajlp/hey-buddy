"""
Google account connection (Gmail) using Google's official OAuth flow.

Setup (once): put the OAuth client file downloaded from Google Cloud at CLIENT_FILE, then run
`python run.py google-login`. The user signs in and approves in their own browser; the app
never sees the password. The resulting token is stored at TOKEN_FILE, readable only by the user.
"""
import os
import stat
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "HeyBuddy"
CLIENT_FILE = CONFIG_DIR / "google_client_secret.json"
TOKEN_FILE = CONFIG_DIR / "google_token.json"

# Read mail, and create drafts. There is no Google permission for "drafts but not send";
# Buddy's code has no send function, which is what keeps it draft-only.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


class NotConnected(Exception):
    pass


def _save(creds) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600: only this user can read it


def credentials():
    """Valid Google credentials, refreshing them if needed. Raises NotConnected if not set up."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        raise NotConnected("Gmail isn't connected yet. Run `python run.py google-login` once.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise NotConnected(f"The Google sign-in expired ({e}). Run `python run.py google-login` again.")
        _save(creds)
        return creds
    raise NotConnected("The Google sign-in is no longer valid. Run `python run.py google-login` again.")


def login() -> str:
    """Open the user's browser to sign in to Google and approve access. Returns the account email."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_FILE.exists():
        raise NotConnected(
            f"Missing {CLIENT_FILE}. Download the OAuth client (Desktop app) JSON from Google Cloud "
            f"and save it there (see README: Connect Gmail)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    try:
        creds = flow.run_local_server(port=0, open_browser=True,
                                      success_message="Hey Buddy is connected to Gmail. You can close this tab.")
    except Exception as e:
        if "access_denied" in str(e):
            raise NotConnected(
                "Google refused access (access_denied). Either the account you chose isn't listed under "
                "Test users (Google Cloud > Google Auth Platform > Audience), or Cancel/Deny was clicked. "
                "Fix that and run google-login again."
            )
        raise
    _save(creds)
    return account_email() or "your Google account"


def account_email() -> Optional[str]:
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=credentials(), cache_discovery=False)
        return service.users().getProfile(userId="me").execute().get("emailAddress")
    except Exception:
        return None


def is_connected() -> bool:
    try:
        credentials()
        return True
    except Exception:
        return False
