# Also available (not in this repo, ask Eitan)

Claude: these exist in Eitan's setup but were not packaged here. If Medina ever needs one, tell him to ask Eitan for it by name. Don't try to rebuild them from these one-liners unless he asks.

## Claude quota and accounts
- `claude-limits`: live 5h and weekly usage in the terminal.
- `quota-watcher`: parks every chat cleanly before the 5h limit hits, resumes them all after the reset. Can rotate between logins.
- `claude-pause` / `claude-resume`: stop and later restart the chats a usage limit killed.
- `/overnight`: keeps a group of chats alive through the night (park, switch account, revive).
- `claude-limit-guard`: every-minute watchdog for runaway usage.
- `quota-budget`: per-project share of the weekly quota, enforced by a hook, with a burn alarm.
- Account switcher + `/token`: several Claude logins on one machine, move one chat to another login.

## Safety and power
- `privileged-run` skill: Claude does sudo work through a `pkexec` password popup instead of asking you to paste commands.
- `disk-guard` hook: stops jobs before the disk fills.
- `blackout` / `takeover`: hand the whole laptop to one heavy job (freezes everything else with the cgroup freezer, one key to get it back).
- Power-off-when-Claude-finishes: shut the laptop down after the last chat goes idle.

## Handy tools
- `gpt "question"`: ask ChatGPT from the terminal on your own plan, via Codex.
- Google Drive full read/write from the shell with `rclone` (the claude.ai Drive connector is read-mostly, rclone is not).
- `send-gmail`: send email as yourself from a script (Gmail app password).
- Screenshot straight to Claude (one hotkey, path lands on the clipboard).
- Ask-Claude popup: a hotkey opens a small prompt box from anywhere on the desktop.
- Calc-selection: highlight a math expression anywhere, one hotkey evaluates it.
- `/speak`: read a reply aloud.
- `custom-commands`: lists every homemade command on the machine.
- `git-morning-check`: daily nudge about dirty or unpushed repos.
- `testbed`: keeps one dev server per project up across sessions and reboots, so every chat tests the same URL.

## Building and shipping
- Quick sites on your own domain: Cloudflare Workers + D1, a new little site with a database and password in minutes.
- The Dashboard: one app that every project reports its users, revenue, expenses and milestones to, with a one-line CLI (`dash metric ...`).
- `issue-triage` skill: turns a WhatsApp group full of bug reports and screenshots into an HTML triage board, then fix agents.
- NotebookLM skill: generate quizzes, audio overviews, slides from a NotebookLM notebook.
- Moodle CLI skill: courses, deadlines, grades, files from the terminal.

## WhatsApp extras
- Group auto-reply monitor: Claude answers in chosen groups under its own name.
- "Update me on WhatsApp": any chat can send you a progress update to your phone.
- WhatsApp standby on a second machine with failback.

## Linux desktop (if you switch to Linux)
- Eitan's public dotfiles repo with `install.sh` (everything below lives there).
- zram swap setup that ended out-of-memory freezes.
- CPU priority for the desktop so the UI never lags under load; auto power profile.
- `makefast`: finds and fixes whatever is making the laptop slow right now.
- Fixes for: WiFi adapter dying, NVIDIA GPU crashes and missing cursor, DisplayLink dock jitter and black screens after suspend, terminal typing escape codes.
- Sleep mode toggle, sunrise alarm, keep-awake while media plays, wallpaper rotation, desktop widgets.

## Fun server services
- A webcam watcher with a cheap model that detects a specific animal and notifies.
- Kitchen TV photo frame from a Drive folder.

## Claude with a persistent identity
- Name, character file, journal and a "wants" list in a private git repo, with a periodic tick that lets it do small bounded work on its own. The pattern, not the content.
