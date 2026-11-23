---
name: to-laptop
description: Move THIS chat back from the server to the laptop as is. Use when the human says /to-laptop.
---
Run from the chat's repo directory (inside a git repo; if the chat's folder is not a repo, pass `--cwd <the one project folder>`, never a parent folder that holds several projects): `chat-move laptop <session-id>` (session id = this chat's transcript name under ~/.claude/projects). It reuses the excludes remembered from the move to the server.

chat-move never deletes anything on the laptop and keeps a copy of every file it overwrites (on the laptop, in ~/.local/state/chat-move/backups/). It first prints a dry-run summary. If it prints ABORTED (the laptop has top-level folders the server copy lacks, which means the server copy is partial, or too many overwrites), STOP: show the human those summary lines and ask; rerun with `--force` only after their explicit yes. Never work around it with a hand-written rsync.

Then tell them the tmux name printed and that this server copy stops now, then stop.
