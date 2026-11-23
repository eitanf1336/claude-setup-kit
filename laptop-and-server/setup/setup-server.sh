#!/bin/bash
# Run ON THE SERVER, as your normal user (needs sudo), after a fresh Ubuntu Server install
# and after you can already ssh in. Idempotent: safe to run twice. Read it before running it.
set -euo pipefail

echo "== packages"
sudo apt-get update
sudo apt-get install -y openssh-server tmux rsync git curl jq unattended-upgrades \
    python3 python3-venv build-essential sqlite3 ufw

echo "== lid closed must never suspend a server"
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/10-server.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo systemctl restart systemd-logind || true

echo "== passwordless sudo for this user (so Claude can administer the box over ssh)"
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/90-$USER-nopasswd >/dev/null
sudo chmod 440 /etc/sudoers.d/90-$USER-nopasswd

echo "== ssh: keys only (make sure your key is already in ~/.ssh/authorized_keys!)"
if [ -s "$HOME/.ssh/authorized_keys" ]; then
  sudo tee /etc/ssh/sshd_config.d/10-keys-only.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
  sudo systemctl reload ssh
else
  echo "   no authorized_keys yet: skipped. From the laptop run: ssh-copy-id -i ~/.ssh/id_ed25519_server.pub USER@SERVER"
fi

echo "== unattended security upgrades"
echo 'APT::Periodic::Update-Package-Lists "1"; APT::Periodic::Unattended-Upgrade "1";' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null

echo "== linger: user services run with nobody logged in"
sudo loginctl enable-linger "$USER"

echo "== tailscale"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
echo "   now run:  sudo tailscale up --hostname my-server   (opens a login link; use the SAME account as the laptop)"

echo "== optional: uv (python version + venv manager) and node"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

mkdir -p "$HOME/bin" "$HOME/.config/systemd/user" "$HOME/.local/state"
echo "done. Next: install Claude Code on the server and log in THERE (see GUIDE.md, 'Claude on the server')."
