#!/bin/bash
# Restart the bot only once it has no claude run in flight.
#
# The unit is KillMode=mixed, so restarting while a reply is being generated
# SIGKILLs that claude and the user never gets their answer. Worse, the pending
# message queue lives in memory, so a restart also drops anything queued but
# not yet started. A claude run is a direct child of bot.py, so "no children"
# means idle, and staying idle for a grace period means the queue has drained.
# The grace period also covers the short gap between a claude exiting and
# bot.py POSTing its reply to the bridge; restarting in that gap eats the reply.
#
# Run this OUTSIDE the service cgroup (systemd-run --user), or it kills itself.
set -u
UNIT="${1:-claude-whatsapp.service}"
QUIET_CHECKS="${CWB_QUIET_CHECKS:-10}"   # consecutive idle polls required
INTERVAL="${CWB_QUIET_INTERVAL:-2}"      # seconds between polls
MAX_WAIT="${CWB_MAX_WAIT:-3600}"         # stop waiting after this long

idle=0
elapsed=0
while :; do
    mainpid=$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)
    if [ -z "$mainpid" ] || [ "$mainpid" = "0" ]; then
        break                            # not running; just start it
    fi
    if pgrep -P "$mainpid" >/dev/null 2>&1; then
        idle=0                           # a claude is working; keep waiting
    else
        idle=$((idle + 1))
        [ "$idle" -ge "$QUIET_CHECKS" ] && break
    fi
    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
        # Never guillotine a long job just to pick up a code change. Bail and
        # let the next natural restart load it.
        echo "still busy after ${elapsed}s; leaving $UNIT alone"
        exit 1
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
done

echo "restarting $UNIT (idle_polls=$idle waited=${elapsed}s)"
exec systemctl --user restart "$UNIT"
