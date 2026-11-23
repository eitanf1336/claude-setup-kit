#!/usr/bin/env bash
# Install the claude-c board and friends. Linux or WSL2 only. Safe to re-run.
# Usage: bash install.sh [--gnome]   (--gnome adds tiler, session restore, timers)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
mkdir -p "$BIN" "$HOME/.claude/chats" "$HOME/.claude/commands"

for need in python3 claude; do
  command -v "$need" >/dev/null || echo "WARNING: $need not found on PATH"; done
command -v jq >/dev/null || echo "WARNING: jq missing (the status line needs it): sudo apt install jq bc"

echo "==> board"
cp "$HERE/board/chats.py" "$HERE/board/projcolor.py" "$HOME/.claude/chats/"
install -m 0755 "$HERE/bin/claude-c" "$BIN/claude-c"
ln -sf "$BIN/claude-c" "$BIN/claude-custom"
for f in claude-c-file fatchats prompts claude-running compact-ask; do
  install -m 0755 "$HERE/bin/$f" "$BIN/$f"; done

echo "==> status line + slash commands"
if [ -e "$HOME/.claude/statusline.sh" ] && ! cmp -s "$HERE/statusline/statusline.sh" "$HOME/.claude/statusline.sh"; then
  echo "    SKIP statusline.sh (you already have one, not overwriting)"
else
  install -m 0755 "$HERE/statusline/statusline.sh" "$HOME/.claude/statusline.sh"
fi
cp "$HERE"/commands/*.md "$HOME/.claude/commands/"
echo '    now merge into ~/.claude/settings.json (see GUIDE.md): statusLine, cleanupPeriodDays'

if [ "${1:-}" = "--gnome" ]; then
  echo "==> GNOME extras"
  for f in claude-session claude-new-chat keys-cheatsheet run-once-term; do
    install -m 0755 "$HERE/bin/$f" "$BIN/$f"; done
  EXT="$HOME/.local/share/gnome-shell/extensions/terminal-tiler@claudekit.local"
  rm -rf "$EXT"; mkdir -p "$(dirname "$EXT")"
  cp -r "$HERE/gnome-terminal-tiler" "$EXT"
  glib-compile-schemas "$EXT/schemas"
  mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/autostart"
  cp "$HERE"/systemd/* "$HOME/.config/systemd/user/"
  cp "$HERE/autostart-claude-session-restore.desktop" "$HOME/.config/autostart/claude-session-restore.desktop"
  systemctl --user daemon-reload
  systemctl --user enable --now claude-session-save.service claude-session-save.timer fatchats.timer
  echo "    optional, uses a Haiku courier per fat chat: systemctl --user enable --now compact-ask.timer"
  echo "    LOG OUT AND IN (Wayland), then: gnome-extensions enable terminal-tiler@claudekit.local"
fi
case ":$PATH:" in *":$BIN:"*) ;; *) echo "NOTE: add $BIN to PATH";; esac
echo done
