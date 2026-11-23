#!/usr/bin/env bash
# install.sh: WhatsApp <-> Claude Code, end to end (Linux / WSL2 with systemd).
#
#   ./install.sh                  bridge + MCP server + skill + helper scripts
#   ./install.sh --with-bot       ... plus the "My Claude" self-group bot
#   ./install.sh --with-bot --with-doctor    ... plus the self-healing watchdog
#   ./install.sh --with-monitor   ... plus the tool-less auto-replier (optional)
#
# Idempotent. No root needed (Go and uv go into ~/.local). Read GUIDE.md first.
# Nothing is started that talks to WhatsApp until YOU scan the QR.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WA_REPO="${WA_REPO:-$HOME/code/whatsapp-mcp}"
BOT_DIR="${WA_BOT_DIR:-$HOME/code/claude-whatsapp-bot}"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.claude/skills/whatsapp"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/whatsapp-claude"
BOT_CFG="$HOME/.config/claude-whatsapp"
UNIT_DIR="$HOME/.config/systemd/user"
GO_VER="1.26.0"
WITH_BOT=0; WITH_DOCTOR=0; WITH_MONITOR=0
for a in "$@"; do case "$a" in
  --with-bot) WITH_BOT=1;; --with-doctor) WITH_DOCTOR=1;; --with-monitor) WITH_MONITOR=1;;
  *) echo "unknown flag $a"; exit 1;; esac; done

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
have_systemd() { systemctl --user show-environment >/dev/null 2>&1; }

# --- toolchain ---------------------------------------------------------------
export PATH="$HOME/.local/go-sdk/go/bin:$BIN_DIR:$PATH"
if ! command -v go >/dev/null 2>&1; then
  say "installing Go $GO_VER into ~/.local/go-sdk"
  arch=amd64; [ "$(uname -m)" = aarch64 ] && arch=arm64
  mkdir -p "$HOME/.local/go-sdk"
  curl -fsSL "https://go.dev/dl/go${GO_VER}.linux-${arch}.tar.gz" | tar -C "$HOME/.local/go-sdk" -xz
fi
if ! command -v uv >/dev/null 2>&1; then
  say "installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# C compiler is needed: the bridge uses go-sqlite3 (cgo).
command -v gcc >/dev/null 2>&1 || { echo "gcc is required (sudo apt install build-essential)"; exit 1; }

# --- upstream + our patch ----------------------------------------------------
if [ ! -d "$WA_REPO/.git" ]; then
  say "cloning lharries/whatsapp-mcp into $WA_REPO"
  git clone https://github.com/lharries/whatsapp-mcp.git "$WA_REPO"
  # Our patch was made against this upstream commit (their last at the time).
  git -C "$WA_REPO" checkout -q 7d6a06d 2>/dev/null || true
fi
say "applying fork patch (LID fixes, /api/send extras, status, media fix, ...)"
( cd "$WA_REPO"
  if git apply --check "$HERE/patches/0001-fork-changes-on-top-of-upstream.patch" 2>/dev/null; then
    git apply "$HERE/patches/0001-fork-changes-on-top-of-upstream.patch"
  else
    echo "    (already applied, or upstream moved on: see GUIDE.md 'patch does not apply')"
  fi )

say "bumping whatsmeow to latest (fixes 'Client outdated (405)') and building"
( cd "$WA_REPO/whatsapp-bridge"
  if ! { go get go.mau.fi/whatsmeow@latest && go mod tidy && go build -o whatsapp-bridge-bin .; }; then
    echo "    latest failed to build; falling back to the pinned go.mod we know worked"
    cp "$HERE/patches/go.mod.pinned-reference" go.mod
    rm -f go.sum; go mod tidy; go build -o whatsapp-bridge-bin .
  fi )

# --- helpers, skill, MCP -----------------------------------------------------
say "installing helper scripts to $BIN_DIR"
mkdir -p "$BIN_DIR" "$CFG_DIR"
install -m 755 "$HERE"/bin/wa-* "$BIN_DIR"/
printf 'WA_REPO="%s"\n' "$WA_REPO" > "$CFG_DIR/config"

say "installing Claude skill to $SKILL_DIR"
mkdir -p "$SKILL_DIR"; cp "$HERE/skill/SKILL.md" "$SKILL_DIR/SKILL.md"

if command -v claude >/dev/null 2>&1; then
  say "registering MCP server 'whatsapp' (user scope)"
  claude mcp add whatsapp --scope user -- "$(command -v uv)" --directory "$WA_REPO/whatsapp-mcp-server" run main.py 2>/dev/null \
    || echo "    (already registered)"
else
  echo "    'claude' CLI not found: register the MCP server by hand (see GUIDE.md)"
fi

# --- bridge as a supervised service -----------------------------------------
if have_systemd; then
  say "installing whatsapp-bridge.service (user unit)"
  mkdir -p "$UNIT_DIR"
  sed "s|__WA_REPO__|$WA_REPO|g" "$HERE/bin/whatsapp-bridge.service" > "$UNIT_DIR/whatsapp-bridge.service"
  systemctl --user daemon-reload
  loginctl enable-linger "$USER" 2>/dev/null || true
  echo "    NOT started yet: link your phone first (wa-reconnect or GUIDE.md section 4),"
  echo "    then: systemctl --user enable --now whatsapp-bridge.service"
else
  echo "    no systemd --user here (WSL2 needs systemd=true, see GUIDE.md); run the bridge with wa-bridge-ctl start"
fi

# --- bot ---------------------------------------------------------------------
install_unit() {  # $1 = unit file in bot/, $2 = placeholder, $3 = script path
  sed "s|$2|$3|g" "$HERE/bot/$1" > "$UNIT_DIR/$1"
}
if [ "$WITH_BOT" = 1 ] || [ "$WITH_MONITOR" = 1 ]; then
  say "installing bot files to $BOT_DIR"
  mkdir -p "$BOT_DIR" "$BOT_CFG"
  cp "$HERE"/bot/bot.py "$HERE"/bot/monitor.py "$HERE"/bot/wa-history "$HERE"/bot/wa-monitor \
     "$HERE"/bot/restart-when-idle.sh "$HERE"/bot/*.service "$BOT_DIR"/
  chmod +x "$BOT_DIR"/wa-history "$BOT_DIR"/wa-monitor "$BOT_DIR"/restart-when-idle.sh
  ln -sfn "$BOT_DIR/wa-history" "$BIN_DIR/wa-history"
  ln -sfn "$BOT_DIR/wa-monitor" "$BIN_DIR/wa-monitor"
fi
if [ "$WITH_BOT" = 1 ]; then
  [ -f "$BOT_CFG/bot.env" ] || cat > "$BOT_CFG/bot.env" <<'ENVEOF'
# Bot settings (see GUIDE.md). Name of the WhatsApp group you talk to the bot in:
CWB_GROUP_NAME=My Claude
# FULL ACCESS: a message in your group can run any command on this machine.
# Safer (but the bot can then use almost no tools): --permission-mode default
CWB_PERMISSION_ARGS=--dangerously-skip-permissions
ENVEOF
  have_systemd && { install_unit claude-whatsapp.service __BOT_PY__ "$BOT_DIR/bot.py"
                    systemctl --user daemon-reload; }
  echo "    bot installed, NOT started. Create the group, then:"
  echo "    systemctl --user enable --now claude-whatsapp.service"
fi
if [ "$WITH_MONITOR" = 1 ]; then
  [ -f "$BOT_CFG/monitor.env" ] || cat > "$BOT_CFG/monitor.env" <<'ENVEOF'
CWM_OWNER_NAME=YOUR_FIRST_NAME
# CWM_ASSISTANT_NAME=YOUR_FIRST_NAME's Claude
ENVEOF
  have_systemd && { install_unit claude-whatsapp-monitor.service __MONITOR_PY__ "$BOT_DIR/monitor.py"
                    systemctl --user daemon-reload; }
  echo "    monitor installed, NOT started (arms nobody until you run: wa-monitor add <chat>)."
fi
if [ "$WITH_DOCTOR" = 1 ]; then
  say "installing the doctor (watchdog)"
  install -m 755 "$HERE/doctor/claude-whatsapp-doctor" "$BIN_DIR/claude-whatsapp-doctor"
  if have_systemd; then
    cp "$HERE"/doctor/claude-whatsapp-doctor.{service,timer} "$UNIT_DIR/"
    systemctl --user daemon-reload
    systemctl --user enable --now claude-whatsapp-doctor.timer
  fi
fi

cat <<EOT

Done. Next:
  1. Link your phone:  wa-reconnect   (GUI)   or run the bridge in a terminal and scan the ASCII QR (headless)
  2. Start a NEW Claude Code session so the whatsapp MCP tools load.
  3. Read GUIDE.md for the bot, the doctor and the gotchas.
EOT
