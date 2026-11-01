#!/bin/sh
set -eu
B=/data/opt/kas-command-router
V=/data/opt/alisa-standalone
# Stop only the matching router processes. Never signal the voice daemon.
if [ -f "$B/supervisor.pid" ]; then
  pid=$(cat "$B/supervisor.pid")
  case "$pid" in ''|*[!0-9]*) exit 1;; esac
  if [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -q "$B/supervise.sh"; then
    kill "$pid"
    n=0
    while kill -0 "$pid" 2>/dev/null; do n=$((n+1)); [ "$n" -lt 50 ] || exit 1; sleep .1; done
  fi
fi
exec 8>/tmp/kas-command-router-supervisor.lock
flock -n 8 || { echo 'Router supervisor is still running' >&2; exit 1; }
exec 7>/tmp/kas-command-router-worker.lock
flock -n 7 || { echo 'Router worker is still running' >&2; exit 1; }
exec 9>/tmp/kas-control-switch.lock
flock -x 9
if [ -f "$B/commands.original" ]; then
  cp "$B/commands.original" "$V/commands.txt.router-restore"
  chmod 600 "$V/commands.txt.router-restore"
  mv "$V/commands.txt.router-restore" "$V/commands.txt"
fi
rm -f "$B/status.json"
echo 'Native commands restored; voice process unchanged. Disable the router boot hook before reboot.'
