# Laptop + always-on home server: a guide for your Claude

You (Claude) are reading this to help your human build and run a two-machine setup:

- the **laptop**: where the human works. Chats, editor, browser, screens, GPU, all code editing.
- the **server**: an old laptop running headless Ubuntu 24/7, reached over Tailscale. It runs the things that must never stop (bots, dashboards, collectors, overnight jobs) and can host Claude chats that keep going while the laptop is shut.

Everything here was learned the hard way by running this setup for months. Files referenced below sit next to this guide. Replace `YOUR_USER` and the host placeholders everywhere. Never put real IPs, tokens or keys into a repo you publish.

## 1. The five rules (put them in your CLAUDE.md)

1. **Code lives on the laptop.** The server runs rsynced copies at the SAME paths. Never edit code on the server; edit on the laptop, commit, then deploy. Virtualenvs are never copied: rebuild them on the server (`uv` works well).
2. **One owner per thing.** A service, a bot session, a wallet, a Claude login, a VPN login: it runs on exactly ONE machine. The other machine's copy stays `disable`d (or `mask`ed) as the rollback. Never start both. Two owners means double trades, double messages, or a login that keeps logging the other one out.
3. **localhost keeps working.** When a service moves to the server, `server-link` forwards the same port from the laptop's `127.0.0.1` to the server's `127.0.0.1`, so every bookmark, script and `localhost:PORT` URL keeps working without edits. But its files and database now live on the server: read or change them with `ssh server`.
4. **Where work goes.** Long, unattended, overnight work: server. Anything touching the human's screen, browser, GPU, or laptop-only files: laptop.
5. **Check before assuming.** Before assuming a service runs locally, run `server` (health and services) or `cat ~/.config/server-link/ports` (what moved).

Add this to the laptop's `~/.claude/CLAUDE.md` in the human's words, plus: "Before deploying to, adding a service to, or moving anything onto the server, read GUIDE.md section 6."

## 2. What works from Windows, and what needs Linux

The human is on Windows today and may switch to Linux. Be honest about the split.

| Piece | Windows (native) | WSL2 (Ubuntu inside Windows) | Linux laptop |
|---|---|---|---|
| Tailscale to reach the server | yes (Windows app) | uses the Windows one | yes |
| `ssh server` (OpenSSH client is built in) | yes | yes | yes |
| VS Code Remote-SSH / terminal into the server | yes | yes | yes |
| **Running Claude ON the server** over ssh + tmux | **yes, the easy path** | yes | yes |
| `server`, `server-link`, `chat-move` (bash scripts) | no | **yes** | yes |
| `server-link` (ssh -L forwards) | `ssh -L` by hand works | yes, as a script or unit | yes |
| systemd user units on the laptop side | no | only with `systemd=true` in `/etc/wsl.conf` (then `wsl --shutdown`) | yes |
| Moving a running chat (`chat-move`) | no | yes, if Claude Code runs inside WSL | yes |

Practical advice:

- **On Windows today, the highest-value setup is: the server does the work, the Windows machine is a thin client.** Install Tailscale on both, `ssh` into the server, run `tmux new -s main` there, start `claude` inside it. Close the Windows laptop, the chat keeps going; reattach with `ssh -t server tmux attach -t main`. Rules 1 to 3 then collapse to "the code lives on the server, backed by git". That is a legitimate simpler variant.
- If the human keeps working in native Windows with code on Windows, deploy with `git push` plus `git pull` on the server (rsync is not native on Windows; it exists in WSL and Git Bash is unreliable for it).
- Forwarding a port by hand on Windows: `ssh -N -L 8000:127.0.0.1:8000 server` in a terminal.
- For the full toolkit on the laptop side, use **WSL2 with systemd enabled**: put `[boot]` / `systemd=true` in `/etc/wsl.conf`, run `wsl --shutdown` once, then everything in `bin/` and `systemd-user/` works as on Linux. Tailscale stays on the Windows side; WSL reaches the tailnet through Windows networking (if `ssh server` from WSL cannot resolve the MagicDNS name, use the server's `100.x` address in `~/.ssh/config`).
- Paths: `chat-move` assumes the same absolute path on both machines under `$HOME`. Inside WSL that is `/home/you/...` on both sides. Do not use `/mnt/c/...` paths for repos you move (slow, wrong line endings, no inotify).
- The server's side is always Linux. Hooks, scripts and systemd units run there.

## 3. Turn an old laptop into the server

Hardware: any laptop with 8 GB or more RAM and an SSD. Plug it into mains. A wired link beats WiFi (see gotchas). If the battery is old, cap its charge at 80% if the vendor offers it (`/sys/class/power_supply/BAT0/charge_control_end_threshold` on many laptops).

1. **Install Ubuntu Server** (current LTS) from a USB stick. Back up anything on the disk first and get the human's explicit yes before wiping: you will erase it.
2. **Get ssh in.** On the laptop: make a dedicated key (`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_server`), then `ssh-copy-id` it to the server over the home LAN.
3. **Run `setup/setup-server.sh` on the server** (read it first). It installs packages, ignores the lid switch, masks suspend, grants passwordless sudo, turns off password ssh (only if a key is already installed), enables unattended security upgrades, enables **linger** (user services run with nobody logged in), and installs Tailscale and `uv`.
4. **Tailscale on both machines**, same account: `sudo tailscale up --hostname my-server`. From then on, ssh over the tailnet name or its `100.x` address, from anywhere. Add the two `Host` blocks from `config/ssh_config.example` to the laptop's `~/.ssh/config`. In the Tailscale admin you can disable key expiry for the server so it never needs a re-login.
5. **Let the server reach back** to the laptop (`Host laptop` block, key only, Tailscale only; never expose ssh to the internet or home WiFi). This is what lets a server-side Claude fetch a laptop file or screenshot.
6. **Copy the scripts** from `bin/` to `~/bin/` on the laptop (and `chat-move`, `server-now`, `server-disk-guard` to `~/bin/` on the server too). Put `config/config.example` at `~/.config/laptop-and-server/config`.
7. **Install the laptop units**: `systemd-user/server-link.service` and `ssh-mux-server.service` into `~/.config/systemd/user/`, then `systemctl --user daemon-reload && systemctl --user enable --now server-link ssh-mux-server`. Put `config/server-link-ports.example` at `~/.config/server-link/ports`.
8. **Disk guard on the server** (`systemd-server/server-disk-guard.*` plus `bin/server-disk-guard`): a full disk is silent, every unit still says "active" while writes fail.
9. **Claude Code on the server**, with its OWN login (section 8).
10. Test: `server` should print uptime, RAM, disk and services. `ssh server tmux new -d -s t 'sleep 60'` then close the lid on the server and check it still answers.

Server facts worth knowing: boot happens before WiFi, so services that fetch from the network need `After=network-online.target` + `Wants=network-online.target` (a collector that caches one failed fetch shows empty data until restarted). The server will show a login prompt on its own screen; that is fine, or use the status screen (section 9).

## 4. The `server` helper

`bin/server` is one door to the server:

```
server              health: uptime, RAM, disk, load, temp, running user services, failed units, timers
server ssh          shell on the server
server logs UNIT    journal of a user unit (default 60 lines)
server restart UNIT restart a user unit and show its status
server link         which ports are forwarded
server run CMD...   run a command there
```

It reads `SERVER_HOST` from `~/.config/laptop-and-server/config` (default alias `server`). When something "does not work", run `server` first.

## 5. server-link: localhost keeps working

`bin/server-link` is a long-running `ssh -N -L 127.0.0.1:P:127.0.0.1:P server` for every port in `~/.config/server-link/ports`, kept alive by `server-link.service`. Add a port, then `systemctl --user restart server-link`. The port must be FREE on the laptop first: stop and disable the laptop copy of the service before forwarding its port.

Trap (already handled in the script): it passes `ControlMaster=no -o ControlPath=none`. If your ssh config multiplexes the server host, a multiplexed client hands its `-L` to the shared master and exits at once; systemd restarts it forever while the forward lives in someone else's master and dies with it.

Always bind services on the server to `127.0.0.1`, never `0.0.0.0`. The tunnel is the access control.

## 6. Deploying, and adding a new always-on service

Deploy a code change:

1. Edit and commit on the laptop.
2. `rsync -a --exclude .git --exclude .venv --exclude __pycache__ <repo>/ server:<same path>/` . **Never add rsync's delete flags.** Exclude the project's data and state files so a deploy cannot overwrite live data.
3. If dependencies changed: rebuild the venv on the server (`uv pip sync`, `npm ci`).
4. `server restart <unit>`, then `server logs <unit>`.

Checklist for a NEW always-on service (do all of it):

1. Unit file in `~/.config/systemd/user/` on the server (template: `systemd-server/service-template.service`). Set `Environment=PATH=%h/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin` if it runs `claude`, `uv` or `node`. Add `After/Wants=network-online.target` if it touches the network. `systemctl --user daemon-reload && systemctl --user enable --now NAME`.
2. Bind to `127.0.0.1`. If the laptop needs it, append the port to the ports file and restart `server-link`.
3. Copy the unit file into your dotfiles/infra repo and commit: the server must be rebuildable from git.
4. If a laptop copy existed: `systemctl --user disable --now` it there (rule 2).
5. Set its word on the status screen (section 9) while it works.
6. If the service sends alerts, remember a headless box has no desktop: popups (`notify-send`, `zenity`) go nowhere. Alert through a channel that reaches the human (a chat message, email, or a `ssh laptop notify-send ...` hop). Do not rely on popups for anything critical.
7. Update the "what runs where" list in your own notes.

Long jobs: start them as a named unit, `systemd-run --user --unit=myjob CMD`, so they survive logout, have a stable name for `server logs`, and can be listed or stopped. If the laptop has any "freeze everything" tool (a render/blackout mode that uses the cgroup freezer), add the unit's name to that tool's keep list the moment you start the job, or the freeze will pause it.

## 7. Moving a running chat between machines

`bin/chat-move <server|laptop> [session-id] [--cwd DIR] [--exclude PAT ...] [--force] [--dry-run]` plus the skills `skills/to-server` and `skills/to-laptop` (copy them to `~/.claude/skills/`, and `chat-move` to `~/bin` on BOTH machines).

What it does: rsyncs the project directory to the same path on the other machine, copies the chat transcript (and subagent transcripts) into the same `~/.claude/projects/<slug>/`, and resumes the chat there inside `tmux` as `chat-<id8>`. Attach: `ssh -t server tmux attach -t chat-<id8>`; `Ctrl+b d` detaches and leaves it running. Then the old copy must stop (one owner per chat).

The story behind its safety rules: an early version synced a project folder in delete mode from a partial copy, run from a non-git parent directory that held several projects. It treated the parent as the project root and wiped 6 of 8 folders. So now:

- it never deletes anything on the receiver; every file it overwrites is first kept in `~/.local/state/chat-move/backups/<date>-<id8>/` on the receiver;
- it dry-runs first and ABORTS if the receiver has top-level entries the sender lacks (the sender is a partial copy) or if more than 25 files would be overwritten (`CHAT_MOVE_MAX_OVERWRITE`); `--force` only with the human's yes;
- it refuses a project root that is not inside a git repo unless `--cwd` names it, refuses `~` and `~/code`, and refuses a non-git parent of several repos;
- it remembers the excludes per chat so the way back is symmetric.

Gotchas for moves:

- `claude --bg --resume <id>` with extra flags makes a COPY of the session. Resume without flags.
- Never revive a chat with a long-lived headless `claude --resume <id> -p` run with the print wait ceiling set to 0 (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`): the run owns the session id but has no terminal to attach to, so the chat looks alive and is unreachable for as long as it waits. Use tmux, or a background agent you can attach to.
- A moved chat's old subagents are gone; their independent jobs (units, tmux sessions) are not.
- If the server dies, its chat transcripts can be mirrored to the laptop beforehand (a small timer that rsyncs `~/.claude/projects` from the server with `--append --bwlimit`, one-way, read-only) and revived with `claude --resume <id>` from the same cwd.

## 8. Claude on the server

- **Own login per machine.** Do not copy `~/.claude/.credentials.json` between machines. Claude Code rotates the refresh token on every refresh; the same login live on two machines means one of them eventually loses the race and gets logged out, and a losing process can even blank the credentials file. Log in on the server through a browser link inside a server chat (`/login`). If both machines MUST share one account and the human accepts the risk, copying is a stopgap, not a design.
- **One login = one machine** (rule 2 applied). If a machine suddenly demands `/login` with empty tokens in `.credentials.json`, that is the lost-race blanking, not an expired session; restore a recent copy or log in again, and find out which two processes shared the login.
- **Config sync.** A small timer on the laptop that rsyncs `CLAUDE.md`, `settings.json`, `skills/`, `commands/`, `hooks/` to the server every few minutes keeps server chats under the same rules. If it mirrors a directory with delete mode, make it skip when the source looks empty and keep replaced files somewhere. Do not sync credentials.
- **A server chat that will not open**: look at its tmux pane first, `ssh server tmux capture-pane -pt chat-<id8>`. Two known causes: it is stuck on Claude Code's first-run theme picker (`hasCompletedOnboarding` was lost from `~/.claude.json`; set it back to true and relaunch the same `claude --resume` in tmux), or the login is dead (needs `/login`).
- Unattended work wants permissions that do not stop to ask. Only enable that on the server, in the human's own chats, by their choice.
- The hooks in section 10 must be installed on the server too.

## 9. The server's status screen (optional but nice)

`status-screen/` is a tiny stdlib Python server (`server.py`, port 7800, localhost only) plus a page that shows uptime, load, RAM, disk, running services, and a **"Running now"** list. Any Claude doing work for a project on the server sets its own word:

```
server-now myproject working "importing data"    # lasts 30 min by default
server-now myproject listening
server-now myproject working "long job" --ttl 2h
server-now myproject --clear
```

Words expire so a crashed job never leaves a stale "working". Port 7800 is in the example ports file, so `server-now` also works from the laptop.

Kiosk on the server's own display: install `cage` and `chromium`, tty1 autologin (`getty-tty1-autologin.conf`), `server-kiosk` started from `~/.bash_profile`. `kiosk-watchdog` (service + timer, every minute) kills the kiosk when the page has not called the server for 3 minutes: a browser renderer once wedged for days and the screen showed a frozen picture, and a watchdog inside the page cannot catch that. Extend `collect_stats()` in `server.py` to show your own numbers (users, revenue, anything real). Show only real numbers.

## 10. Safety hooks

Copy `hooks/delete-guard.py` and `hooks/disk-guard.py` to `~/.claude/hooks/` on BOTH machines, make them executable, and register them in `~/.claude/settings.json` (see `hooks/settings-hooks.example.json`; merge, do not overwrite existing hooks). Run `python3 hooks/test-guards.py` to check them.

- **delete-guard** (PreToolUse on Bash) blocks: rsync with any delete flag, `rclone sync/bisync/purge`, `git clean -f` with `-d` or `-x`, and `rm -r` of `~`, `~/code`, `~/code/*`, `~/code/*/*`, `/`, `/home`. The block message tells you to dry-run, show the human the exact delete count, and get a yes. Only then re-run with the literal marker `# I-CHECKED-DRY-RUN` at the end of the command. It was born from the chat-move wipe above. Adjust `protected()` to your own layout.
- **disk-guard** blocks anything that can wipe or rewrite a disk (`dd of=`, `mkfs`, `wipefs`, `parted`, `sgdisk`, `blkdiscard`, `shred`...) unless every disk it names is listed in `~/.config/disk-guard/allowed` by its `/dev/disk/by-id` path (serial included). Empty file means nothing may be wiped. Put disks you must never touch (data disks, the install USB) in `~/.config/disk-guard/forbidden` as serial substrings. Add a disk to `allowed` only for the minutes of an install, with the human's explicit yes, then remove it.
- The guards read the whole command text, so a command that merely CONTAINS the scary words (an echo, a heredoc that writes a script, a test string) can trip them. When a block fires on text you are only writing to a file, create the file with your editor tool instead of a shell heredoc, or build the string in pieces. Never add the marker just to get past a block.

## 11. Gotchas (all of these happened)

- **`ssh server` hangs forever while the server pings fine**: the shared ssh master was frozen (a terminal that owned it got frozen or killed). `ssh -v` stops at "auto-mux: Trying existing master". Fix: `ssh -O exit server`, or test with `ssh -o ControlPath=none server`. Prevention: `ssh-mux-server.service` owns the master instead of a terminal.
- **A hanging localhost or service after a "freeze everything" mode**: a unit frozen by the cgroup freezer still reports `active`, `start` on it succeeds and does nothing, and `restart` fails with "Cannot perform operation on frozen unit". Thaw it instead of debugging the service. Only relevant if you build such a mode.
- **WiFi drops for a minute or two** on some Intel cards (beacon loss, "ssid-not-found"), which freezes every ssh session. Turn WiFi power save off (`/etc/NetworkManager/conf.d/` with `wifi.powersave = 2`, and `iw dev wlan0 set power_save off`); a cable is the real cure. Check `journalctl -k | grep -E "BEACON-LOSS|deauth|Microcode"` first.
- **Disk full is silent.** Keep `server-disk-guard` running, keep 1 to 2% reserved blocks (`sudo tune2fs -m 2 /dev/...`) so user services still get space, and watch large generated folders.
- **Home internet behind carrier NAT**: Tailscale will use a relay (DERP) when the laptop is on a strict network (campus, office), adding 150 to 250 ms per keystroke. Check with `tailscale ping SERVER`. Never let a bulk copy hog that link: use `rsync --bwlimit=96` (KiB/s) for background mirrors.
- **VPN on the server**: if the server holds a VPN login, the VPN client may rewrite DNS (a dead VPN resolver left behind breaks all name resolution, and Claude says "can't reach the API") and route everything including Tailscale through the tunnel. Pin Tailscale's packets to the home router, restore DHCP DNS from a NetworkManager dispatcher script on every network change, and keep exactly ONE machine connected to the VPN.
- **Suspend that bounces in seconds**: a USB receiver (remote, mouse dongle) can wake the machine; disable its wakeup in `/proc/acpi/wakeup` or udev.
- **Headless means no GUI**: anything needing a display on the server needs an X/Wayland session (`xvfb` for a virtual one). Check before promising a scraper or browser job will run there.
- **Standby / failback idea**: if a second spare machine exists, keep a stopped copy of a critical service there and a small script that starts it and disables the first copy, plus the reverse. The rule stays: only ever one running.

## 12. First-day order of work for you

1. Read this guide and the human's current setup (OS, is Windows native or WSL2, is there a spare laptop).
2. If there is no server yet: sections 3 and 4. Get `server` printing health before anything else.
3. Install the hooks (section 10) before running any bulk file operation.
4. Move ONE small service as a rehearsal using the section 6 checklist, including its port in `server-link`.
5. Only then move chats (section 7) and add the status screen (section 9).
6. Ask the human before: wiping any disk, anything that costs money, anything that messages people on their behalf, and before ever running anything with a delete flag.
