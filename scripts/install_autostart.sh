#!/bin/bash
# Installs Hey Buddy's login items: the server (com.heybuddy.web) and its window (com.heybuddy.window).
# Fills the templates in scripts/launchagents/ with this project's folder and your home folder.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$HOME/Library/Logs"

for label in com.heybuddy.web com.heybuddy.window; do
  sed -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" -e "s#__HOME__#$HOME#g" \
    "$PROJECT_DIR/scripts/launchagents/$label.plist.template" > "$AGENTS/$label.plist"
  plutil -lint -s "$AGENTS/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
  echo "Installed $label"
done
echo "Hey Buddy will now start at every login. Logs: ~/Library/Logs/heybuddy.log"
