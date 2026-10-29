# Hey Buddy

A voice-controlled AI assistant for macOS that runs **on your Mac**: say "Hey Buddy", ask for something,
and it does it (plays music, opens and arranges apps, changes settings, works in Chrome, checks Gmail,
drafts emails, and reads LinkedIn), asking before anything risky.

- **Local models**: chat and tool use through [Ollama](https://ollama.com) (`qwen3-coder`); speech-to-text with
  Whisper large-v3-turbo on Apple Silicon (`mlx-whisper`). Your voice never leaves the Mac.
- **Multi-agent**: a router sends each request to a specialist (Music, Apps, System, Browser, Mail, LinkedIn,
  Talk, Coder) or to a general agent that can read the screen and click. Specialists use fixed-code skills
  (e.g. `play_music`, `window_action`, `youtube_play`) instead of improvising.
- **Hands-free**: wake word, natural pauses, follow-up questions without repeating "Hey Buddy", spoken yes/no
  approvals, and a shared conversation memory across all agents.

---

## ⚠️ Safety and privacy (read first)

Hey Buddy can control your Mac by voice. It's built to be careful, but it is experimental software:

- **It can run commands and click for you.** Shell commands, AppleScript, clicks, and keystrokes need your
  approval (a spoken "yes" approves a whole task). Deleting files, `sudo`, quitting apps, sending/posting/
  buying on web pages, and similar actions always need a click. Safe mode is `safe_mode` in `src/config.py`.
- **Anyone near your Mac can talk to it** while Hands-free is on, especially during the 8-second follow-up
  window. Say "thanks" to end a conversation.
- **Gmail**: read and draft only. Buddy has no send function, but Google's draft permission technically
  allows sending, so treat the connection like any app with mail access. You create your own Google Cloud
  client; nothing is shared with anyone else.
- **LinkedIn**: there's no personal API, so Buddy works in your signed-in browser. LinkedIn's User Agreement
  discourages automated use; use it for occasional, personal requests, never bulk actions.
- **Local only**: the web page and server listen on `127.0.0.1` and reject other websites. Chrome's built-in
  speech recognition (which sends audio to Google) is not used.
- **What's stored on your Mac**: `~/Library/Logs/heybuddy.log` keeps your requests, Buddy's actions, and the
  titles of pages it reads (including incognito pages). `~/Library/Application Support/HeyBuddy/` holds your
  settings, Google sign-in, and the last few conversation turns.

---

## 🚀 Install

**Needs:** a Mac with Apple Silicon, Python 3.12+, [Ollama](https://ollama.com), Google Chrome, and Xcode
Command Line Tools (for the Swift helper).

```bash
git clone <this repository> hey-buddy && cd hey-buddy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ollama pull qwen3-coder:30b
ollama create qwen3-coder-32k -f models/qwen3-coder-32k.Modelfile   # same model, 32k context: fast and fits in memory
swiftc -O src/system/buddy_helper.swift -o src/system/buddy_helper
mkdir -p ~/Library/Application\ Support/HeyBuddy
cp settings.example.json ~/Library/Application\ Support/HeyBuddy/settings.json   # then edit it
```

**Run:**
- Web window with hands-free voice: `.venv/bin/python run.py web`, then open http://127.0.0.1:8765
- Terminal version: `.venv/bin/python run.py cli`
- Diagnostics: `.venv/bin/python run.py status`
- Start at every login: `scripts/install_autostart.sh`

**macOS permissions** (System Settings → Privacy & Security), for the app that runs Buddy (Terminal, or
Python when auto-started):
- **Screen & System Audio Recording**: reading the screen
- **Accessibility**: clicks, keystrokes, and controlling apps
- **Automation**: allow when macOS asks to control System Events, Music, or Chrome
- **Microphone** (in Chrome): for Hands-free

### Personal settings
Copy `settings.example.json` to `~/Library/Application Support/HeyBuddy/settings.json` and fill it in:
- `user_name`: your name, used when Buddy drafts emails and LinkedIn messages for you
- `signoff`: how those drafts are signed (defaults to your first name)
- `speech_hints`: words the speech recognizer should spell right (names, songs, apps you use)

This file, your Google sign-in, and Buddy's conversation memory all stay in that folder, outside the project.

### Auto-start at login
Run `scripts/install_autostart.sh`. It fills the templates in `scripts/launchagents/` with this folder's
location and installs them.

### Connect Gmail (once)
Buddy reads your mail and saves drafts through Google's official Gmail API. It has no send function:
drafts appear in Gmail for you to review and send. You sign in to Google yourself; Buddy never sees your password.

1. Open https://console.cloud.google.com and create a project (e.g. "Hey Buddy").
2. **APIs & Services → Library → Gmail API → Enable.**
3. **Google Auth Platform** (the OAuth consent screen) → Get started: app name "Hey Buddy", your email as
   support and contact email, audience **External** → Create.
4. **Audience → Test users → Add users →** your Gmail address.
5. **Clients → Create client → Application type: Desktop app → Create → Download JSON.**
6. Save the downloaded file as
   `~/Library/Application Support/HeyBuddy/google_client_secret.json`
7. Run `.venv/bin/python run.py google-login`, choose your account, click through "Google hasn't verified
   this app" (it's your own app), and allow access.

While the app is in "Testing", Google expires the sign-in after 7 days (run step 7 again). To avoid that,
use **Audience → Publish app**; for your own account the app can stay unverified.

Permissions requested: read mail, and compose drafts. The sign-in token is stored in
`~/Library/Application Support/HeyBuddy/google_token.json`, readable only by your user account.
Disconnect any time at https://myaccount.google.com/permissions.

### LinkedIn
LinkedIn has no API for personal inboxes or feeds, so the LinkedIn specialist works in your signed-in Chrome:
profile, notifications, messages, network, jobs, search, and drafting messages or posts. It never presses
Send, Post, Connect, or Follow; those always need your click.

### Starts automatically at login
Two LaunchAgents in `~/Library/LaunchAgents/`:
- `com.heybuddy.web.plist`: runs the server (restarts it if it crashes). Logs: `~/Library/Logs/heybuddy.log`.
- `com.heybuddy.window.plist`: runs `scripts/open_window.sh`, which opens the page in its own compact Chrome window on the right edge of the screen (skipped if one is already open).

Only one open page listens for "Hey Buddy" at a time; the newest one takes over the microphone.

Restart the server: `launchctl kickstart -k gui/$(id -u)/com.heybuddy.web`
Stop auto-start: `launchctl bootout gui/$(id -u)/com.heybuddy.web` and `launchctl bootout gui/$(id -u)/com.heybuddy.window`, then delete the two plist files.

### Hands-free voice
In the web HUD, click **🎧 Hands-free** and say "Hey Buddy" followed by your request.
- Pauses are fine: a request is sent once you've paused about a second and it sounds finished; after words
  like "and", "to", "play" it waits up to ~2 s for the rest.
- Conversation: after Buddy answers, it listens ~8 s for a follow-up without "Hey Buddy".
  Say "thanks" / "that's all" / "stop" (or stay quiet) to end it.
- Tuning: `SOFT_END_MS`, `COMPLETE_END_MS`, `HARD_END_MS`, `FOLLOWUP_MS` in `src/web/static/index.html`.

---

## 📁 Architecture Overview

```
voice / text ─► Router ─► specialist agent ─► skills (fixed code) / general tools
                  │            │
   rules first (instant),      └─ "not_my_task" ─► General agent
   else a quick model call
```

- **Router** (`src/agents/router.py`): clear-cut requests ("play…", "open Safari", "volume…") are matched by rules instantly; others are classified by the model (~0.2 s). Requests spanning several areas go to the General agent.
- **Specialists** (`src/agents/specialists.py`): a short prompt plus a few skills each.
  - 🎵 Music: `play_music`, `play_playlist`, `music_control`, `now_playing`
  - 🪟 Apps: `open_app`, `quit_app`, `switch_to_app`, `hide_app`, `list_running_apps`, `window_action`
  - 🌐 Browser: `open_url`, `web_search`, `page_elements`, `click_on_page`, `type_on_page`, `read_page`, `list_tabs`, `switch_tab`, `close_tab`, `browser_navigate` (Chrome/Safari pages are driven through accessibility: links and fields by name)
  - ✉️ Mail: `gmail_search`, `gmail_read`, `gmail_draft` (no sending)
  - 💼 LinkedIn: `linkedin_open`, `linkedin_search`, `linkedin_write` (drafts only) plus page reading/clicking
  - ⚙️ System: `set_volume`, `set_brightness`, `set_dark_mode`, `set_wifi`, `system_status`, `sleep_display`, `take_screenshot`
- **General / Talk / Coder** (`src/agents/*_agent.py`): the general agent has every tool and skill (screen reading, clicking, shell, AppleScript); Talk only looks and answers; Coder helps with code.
- **Orchestrator** (`src/agents/orchestrator.py`): runs the router, the chosen agent, and hand-offs. The UI's Auto mode uses it; Talk / Agent / Coder pin a mode.
- **Skills** (`src/skills/`): deterministic actions registered with `@skill(...)`; `needs_approval=True` makes a skill part of an approved task.
- **Voice** (`src/system/transcriber.py`): Whisper large-v3-turbo on-device; the page detects speech and sends each utterance.

```
src/
├── agents/        router, orchestrator, specialists, base/general/talk/coder agents
├── skills/        registry + music, apps, browser, system_settings skills
├── system/        mac_bridge (macOS APIs), tools (general tools + dispatcher), transcriber, voice, buddy_helper (Swift)
├── core/          Ollama client, types
├── interfaces/    terminal UI
└── web/           FastAPI server + HUD page
```
