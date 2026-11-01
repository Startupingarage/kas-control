#!/bin/sh
set -eu
umask 077
B=/data/opt/kas-command-router
V=/data/opt/alisa-standalone
exec 8>/tmp/kas-command-router-supervisor.lock
flock -n 8 || exit 0
restore() {
  (
    flock -x 9
    if [ -f "$B/commands.original" ]; then
      cp "$B/commands.original" "$V/commands.txt.router-restore"
      chmod 600 "$V/commands.txt.router-restore"
      mv "$V/commands.txt.router-restore" "$V/commands.txt"
    fi
    rm -f "$B/status.json"
  ) 9>/tmp/kas-control-switch.lock
}
child=
finish() {
  trap '' TERM INT HUP
  if [ -n "$child" ]; then kill "$child" 2>/dev/null || :; wait "$child" 2>/dev/null || :; fi
  restore
  rm -f "$B/supervisor.pid"
  exit 0
}
trap finish TERM INT HUP
echo $$ > "$B/supervisor.pid"
# Retained across process/boot failure; never back up an already filtered file.
(
  flock -x 9
  if [ ! -f "$B/commands.original" ]; then
    cp "$V/commands.txt" "$B/commands.original.tmp"
    mv "$B/commands.original.tmp" "$B/commands.original"
  fi
) 9>/tmp/kas-control-switch.lock
restore
while :; do
  "$B/router" 8>&- &
  child=$!
  wait "$child" || :
  child=
  restore
  sleep 2
done
