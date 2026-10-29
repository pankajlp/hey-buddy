#!/bin/bash
# Opens Hey Buddy's page in its own compact browser window once the server is up.
# Runs at login (LaunchAgent com.heybuddy.window) and can be run by hand.
URL="http://127.0.0.1:8765"

# Wait up to 90 s for the server (it starts at login too)
for _ in $(seq 1 90); do
  curl -s -o /dev/null "$URL/" && break
  sleep 1
done

if open -Ra "Google Chrome" 2>/dev/null; then
  # Don't open a second Buddy window if one is already open
  if pgrep -xq "Google Chrome" && osascript -e 'tell application "Google Chrome" to get URL of every tab of every window' 2>/dev/null | grep -q "127.0.0.1:8765"; then
    exit 0
  fi
  # App mode: a standalone window without tabs or address bar, separate from normal browsing
  open -na "Google Chrome" --args --app="$URL"
  # Place it along the right edge of the screen
  for _ in $(seq 1 20); do
    sleep 0.5
    osascript <<'APPLESCRIPT' 2>/dev/null | grep -q done && break
tell application "Finder" to set screenBounds to bounds of window of desktop
set screenWidth to item 3 of screenBounds
tell application "Google Chrome"
  repeat with w in windows
    if URL of active tab of w starts with "http://127.0.0.1:8765" then
      set bounds of w to {screenWidth - 470, 60, screenWidth - 10, 900}
      return "done"
    end if
  end repeat
end tell
APPLESCRIPT
  done
else
  open -a Safari "$URL"
fi
