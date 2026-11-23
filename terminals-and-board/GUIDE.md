# Terminals and the Claude chat board

This guide is for your Claude Code. Read it, then set the pieces up for the user in the order below. Everything here exists to solve one problem: running 10+ Claude Code chats at once without losing track of any of them.

## Why this exists

- Many chats run in parallel. Without a board you forget which one is waiting on you, which is done, and which is dead.
- You read bottom-up. A chat's last line is the only thing guaranteed to be seen, so the board and status line surface state instead of making you open each chat.
- A chat must never be lost: reboots kill terminals, Claude Code deletes old transcripts, and auto-titles rename chats. Several tools here exist only to defend against those.
- Every message re-bills the whole context, so a fat chat is expensive. The status line and `fatchats` nudge you to `/compact`.

## What works where

| Piece | Linux (GNOME) | Linux (other DE) / WSL2 | Windows native |
|---|---|---|---|
| `claude-c` board, `claude-c-file`, slash commands `/done` `/cdone` | yes | yes | no |
| status line (needs `jq`, `bc`, python3) | yes | yes | no (use WSL2) |
| `fatchats`, `claude-running`, `compact-ask` | yes | yes (notify-send needed for popups) | no |
| `prompts` TUI | yes | yes | no |
| `prompts gui`, `keys-cheatsheet` (GTK3 via python3-gi) | yes | needs GTK | no |
| Terminal Tiler extension, `claude-session` restore | GNOME Shell only | no | no |

Windows today: install WSL2 (Ubuntu) and do everything inside it, using Claude Code installed in WSL. The board is a curses TUI and runs fine in Windows Terminal. For the tiler's job use Windows Terminal split panes (Alt+Shift+Plus / Alt+Shift+Minus, or a `wt` command with `split-pane` profiles) or PowerToys FancyZones with one terminal per zone. Session restore has no Windows equivalent; the board itself still reopens any chat in two keystrokes.

Requirements: Claude Code recent enough to have `claude agents --json`, `claude attach` and `claude --bg` (the board is built on background agents). If a flag differs on the user's version, read `claude --help` and adapt `board/chats.py`.

## Layout of this folder

- `board/chats.py` and `board/projcolor.py`: the board (`claude-c`) and per-project colors. Standard library only.
- `bin/claude-c`: 3-line launcher (runs `~/.claude/chats/chats.py`).
- `bin/claude-c-file`: files the current chat as Done / Done, Not Committed and drops the terminal back to the board.
- `commands/`: slash commands `/done`, `/cdone` (call `claude-c-file`) and `/prompts`.
- `statusline/statusline.sh`: the status line.
- `bin/fatchats`, `bin/compact-ask`, `bin/claude-running`: context and cost awareness.
- `bin/prompts`: personal prompt library.
- `bin/keys-cheatsheet`: GNOME overlay listing your shortcuts.
- `bin/claude-new-chat`, `bin/claude-session`, `bin/run-once-term`: GNOME launchers and session restore.
- `gnome-terminal-tiler/`: the GNOME Shell extension.
- `systemd/`, `autostart-claude-session-restore.desktop`: timers and login restore.
- `install.sh`: installs the above (`bash install.sh` for the portable part, `--gnome` for the rest).

## Install

1. Portable part (Linux or WSL2): `bash install.sh`. It copies the board to `~/.claude/chats/`, tools to `~/.local/bin/` (put that on PATH), slash commands to `~/.claude/commands/`, and the status line to `~/.claude/statusline.sh` (never overwrites an existing one).
2. Merge into `~/.claude/settings.json` (do not replace the file):
   ```json
   {
     "statusLine": {"type": "command", "command": "~/.claude/statusline.sh", "padding": 1},
     "cleanupPeriodDays": 3650,
     "autoCompactWindow": 300000
   }
   ```
3. Run `claude-c`. First launch shows every existing chat. Press `n` for a new chat.
4. GNOME only: `bash install.sh --gnome`, log out and in (Wayland extensions never hot-reload), then `gnome-extensions enable terminal-tiler@claudekit.local`. It needs `terminator` installed (the schema's default terminal command; change `terminal-command` with `gsettings` or dconf-editor to use another, but WM_CLASS matching and `claude-session` assume terminator).

## The board: `claude-c`

A curses TUI that lists all chats grouped by category (In Progress, To Test, Later, Failed, Done, Not Committed, Done) with a live status icon from Claude itself (running, needs input, finished, failed). Source of truth for "live" is `claude agents --json`; chat files are only read from `~/.claude/projects`.

Keys: `Enter` open (attaches to a live agent, resumes a finished one), **Ctrl+Z to leave a chat and land back on the board while the agent keeps running**, `n` new chat, `r` rename, `1..6` file into a category (newest at top), `u` undo a move, `s` star, `/` find, `m` move chat to a project, `P` projects panel (add folders, colors, hide), `f` fork, `x` stop, `d` delete (confirms), `Ctrl+R` reload the script, `q` quit. Command line: `claude-c <project>`, `claude-c --new [project]`, `claude-c --list`.

Notes:
- The board only stores tags in `~/.claude/chats/*.json`. Deleting that folder removes the tool and touches no chat.
- Use the 8-char short id for `claude attach`, not the full UUID (the board does this already).
- `/done` and `/cdone` inside a chat file it and return to the board after about 3 seconds.
- Optional features in `chats.py` are inert unless you create their files: per-project Claude accounts (`~/.claude/chats/project_config_dirs.json`), chats mirrored from another ssh host (`~/.claude/remote/<host>/`), per-chat account pins (needs a helper that is not shipped). Ignore them.

## Gotchas (all hit in practice)

- **Transcripts are deleted after 30 days by default.** `cleanupPeriodDays` unset means Claude Code removes every `.jsonl` under `~/.claude/projects/` older than 30 days, and a project whose chats are all gone vanishes from the board entirely. Set `"cleanupPeriodDays": 3650` (above) on day one. If it already happened: `~/.claude/history.jsonl` keeps every prompt you typed (with project and session id) and is not pruned; prompts are recoverable, replies are not.
- **A chat "disappeared" but its file exists.** Claude re-titles a chat when it is relaunched in the background. The board pins the title from when you last left the chat; if one still looks missing, search the jsonl for `aiTitle` and check `categories.json` before assuming loss.
- **Never kill "idle" `claude bg-spare` / `--resume` process trees blindly.** A spare claimed by a real chat keeps the same argv, so it looks like a leak but is a live chat. List titles first.
- **Terminal types `^[[I` / `^[[O` garbage.** Claude leaves focus-reporting and mouse modes on when a session is killed. The board resets terminal modes before each redraw. For a plain shell run `printf '\033[?1004l\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?2004l'` (or `reset`), or put that in `PROMPT_COMMAND`.
- **RAM:** each active background agent costs roughly 700 MB (attach, bg-spare, pty-host processes). Ten at once is several GB. `claude-running` shows what is live and at which effort.
- **GUI launchers must detach** (`setsid -f cmd >/dev/null 2>&1 </dev/null`), otherwise the app dies with the terminal or blocks the prompt. All launchers here do. On Wayland a single-instance app launched from a window that closes immediately may never raise; `run-once-term` lingers a moment for that reason.
- **Do not run GUI tests on the user's live desktop.** Test windows steal focus and swallow what the user is typing. Verify focus first, prefer headless checks.
- **GNOME extension edits need a log out/in** on Wayland. Say so instead of promising it is live.

## Status line

`statusline.sh` shows project chip (colored per project, same color as the board), model and effort, 5h and weekly quota left with reset times, and a context segment that escalates (`compact soon`, `/compact`, `/compact NOW`) relative to the autocompact point. It also writes one small crumb per chat to `~/.claude/state/ctx/`, which `fatchats` reads. Rate-limit fields only exist for subscription plans and appear after the first reply.

## Fat chats and cost

- `fatchats`: lists live chats by context size. `fatchats --notify` sends one desktop notification per chat per threshold; `systemd/fatchats.timer` runs it every 5 minutes.
- `compact-ask`: optional. Every 2 minutes it tells chats nearing autocompact (via a cheap Haiku courier using `SendMessage`) to finish their step, write resume notes and end with "FULLY READY TO COMPACT", so compaction never lands mid-message. It spends a little quota; enable `compact-ask.timer` only if wanted. `compact-ask --dry` shows who it would ask.
- `claude-running`: one row per live Claude with effort and model.
- Rule of thumb: past about 300k tokens in a chat, `/compact` or hand off to a fresh chat with a short brief before big work. Never fork a subagent from a fat chat, it copies the whole context.

## Prompts library

`prompts` keeps reusable prompts as markdown files in `~/.claude/prompts/` (its own private git repo, auto-committed). `prompts` is a TUI, `prompts gui` a GTK popup (bind it to a key, e.g. Ctrl+Shift+P, in GNOME settings), and `/prompts` lets Claude manage it. The popup copies to the clipboard.

## Keys cheatsheet (GNOME)

`keys-cheatsheet` is a GTK overlay that reads your live GNOME custom keybindings from gsettings plus a short list of extension shortcuts. Bind it to Super+K. `--selftest` checks parsing without opening a window. Edit `EXTRAS` for your own shortcuts.

## Terminal Tiler (GNOME Shell extension)

On-demand column tiling so ten terminals stay readable: Super+Return opens a terminal full-screen on the focused monitor, again splits that monitor into equal columns; a focused stray terminal gets absorbed. Super+Ctrl+Return minimises or restores the whole group, Super+Up/Down zooms one terminal over its group, Super+Left/Right reorders, Super+Alt+Left/Right moves focus, dragging a window ejects it. Per monitor, Wayland. Shell versions 45 to 50. The extension exports a D-Bus API (`claudekit.TerminalTiler`: `GetLayout`, `SpawnTiled`) that `claude-session` uses and dumps its column layout to `~/.claude/chats-session/tiler-layout.json` on every change (GNOME does not call `disable()` at session end, so saving there is useless).

Gotcha for anyone editing it: do not watch `notify::minimized` on a Meta.Window (never emitted); use the window manager `minimize`/`unminimize` signals.

## Session restore (GNOME)

A logout kills terminals, boards and the chats inside them; transcripts survive but the desktop comes back empty. `claude-session` fixes that:
- `save` runs every minute (timer) and at session teardown (`claude-session-save.service` ExecStop), writing `~/.claude/chats-session/session.json`.
- `restore` runs from autostart at login and reopens each chat via `claude-c --open <id>` (attach if live, resume if settled), handing each terminal to the tiler so it lands in its old column. It retries missing chats and notifies if any stay missing.
- Named layouts: `claude-session save <name>`, `load <name>`, `configs`. Bind Ctrl+Alt+1..9 and Ctrl+Alt+Shift+1..9 yourself if you want the quick switch (not shipped, it is a gsettings custom keybinding).
- `claude-new-chat` (bind e.g. Ctrl+Alt+Y) opens a terminal already inside a brand-new chat; Ctrl+Z drops to the board.
- Requires terminator, systemd user units, and the tiler extension enabled. Without the tiler, `claude-session` falls back to spawning through `systemd-run`, with no column placement.

Not ported: any account switching, server chat mirroring, and personal boards from the original setup.
