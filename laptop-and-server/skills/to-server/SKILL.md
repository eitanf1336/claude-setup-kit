---
name: to-server
description: Move THIS chat to the home server as is (repo copy to the same path, transcript, resume in tmux there), so the laptop can be shut down. Use when the human says /to-server or "move to the server".
---
Run, from the chat's repo directory (inside a git repo; if the chat's folder is not a repo, pass `--cwd <the one project folder>`, never a parent folder that holds several projects), with this chat's session id (the transcript file name under ~/.claude/projects):

    chat-move server <session-id> [--exclude BIGDIR ...]

Add `--exclude` for huge folders that make no sense to copy (datasets, model checkpoints, node_modules). chat-move remembers them so the move back reuses them.

chat-move never deletes anything on the other machine, keeps a copy of every file it overwrites (there, in ~/.local/state/chat-move/backups/), and first prints a dry-run summary (files to add, files to overwrite). If it prints ABORTED (the server has top-level folders this side lacks, or too many overwrites), STOP: show the human those summary lines and ask; rerun with `--force` only after their explicit yes. Never work around it with a hand-written rsync.

Then tell them in two lines: the tmux name printed (attach with `ssh -t server tmux attach -t <name>`), and that THIS copy stops now (one owner per chat). Then stop working in this chat.
If the work needs credentials or hosts the server cannot reach (another machine's ssh key, a VPN), say so before moving; do not create keys for the human.
