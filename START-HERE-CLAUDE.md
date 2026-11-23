# Start here (for Medina's Claude)

You are setting up Medina's machine to work like Eitan's: many Claude Code chats at once, a board to keep them organized, an always-on home server that runs the long stuff, and Claude reachable from WhatsApp on the phone. Everything in this repo is real code from Eitan's setup, generalized and scrubbed. Nothing here has been tested on a machine other than Eitan's, so read each script before running it and expect small fixes.

## Read in this order

1. `TIPS-FOR-CLAUDE.md`: how Eitan's Claude is told to behave. Propose a `~/.claude/CLAUDE.md` for Medina from it, show it to him, install only what he agrees to.
2. `laptop-and-server/GUIDE.md`: the two-machine setup and the safety hooks.
3. `terminals-and-board/GUIDE.md`: the `claude-c` board and friends.
4. `whatsapp/GUIDE.md`: WhatsApp for Claude, and the phone-to-Claude bot.
5. `ALSO-AVAILABLE.md`: everything else Eitan has. Not shipped, Medina asks Eitan if he wants one.

## Medina's situation

He is on **Windows right now** and has not decided whether to switch to Linux. Plan around that:

- **The server is Linux no matter what.** Any spare laptop or mini PC with Ubuntu Server. This is the piece that works the same whatever his desktop runs, and it is where WhatsApp, the bot and long jobs live. If he has a spare machine, start here.
- **On Windows, use WSL2 (Ubuntu) for everything else.** Enable systemd inside it (a `[boot]` section with `systemd=true` in `/etc/wsl.conf`, then `wsl --shutdown` from Windows). Claude Code, the board, `server-link`, the hooks all run in WSL2. The GNOME Terminal Tiler does not exist on Windows: use Windows Terminal panes or PowerToys FancyZones instead.
- **The simplest Windows path of all:** run Claude Code ON the server, reached over `ssh` + `tmux` from Windows Terminal. Then the Windows side needs almost nothing.
- **If he moves to Linux (Ubuntu GNOME)**, everything here works natively, including the tiler and session restore.

## Suggested first day

1. Install Claude Code (in WSL2 or on Linux) and agree a `CLAUDE.md` with him from the tips.
2. Install the safety hooks (`laptop-and-server/hooks/`) before anything else that moves files.
3. If he has a spare machine: turn it into the server (`laptop-and-server/setup/setup-server.sh`, Tailscale on both ends, ssh keys).
4. Install the board (`terminals-and-board/install.sh`).
5. Put the WhatsApp bridge on the server, link it with a QR code from his phone, then add the bot (`whatsapp/install.sh --with-bot --with-doctor`).

## Rules while you do this

- Ask before anything that sends a message, deletes data, or costs money.
- One machine owns each thing (the WhatsApp session, a Claude login, a service). Never run two copies at once.
- Never commit secrets, the WhatsApp `store/` folder, or credentials to any repo.
- When something breaks and the guide does not cover it, Medina can ask Eitan: most failure modes have already happened to him once.
