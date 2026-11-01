#!/bin/sh
set -eu
HOOK=/etc/init.d/S99zkasroutes
restore_ro=0
cleanup() {
  if [ "$restore_ro" = 1 ]; then mount -o remount,ro /; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if [ -f "$HOOK" ]; then
  grep -q '/data/opt/kas-command-router/supervise.sh' "$HOOK" || exit 1
  options=$(awk '$2 == "/" {print $4}' /proc/mounts)
  case ",$options," in
    *,ro,*) restore_ro=1; mount -o remount,rw / ;;
    *,rw,*) : ;;
    *) exit 1 ;;
  esac
  rm -f "$HOOK"
  sync
  if [ "$restore_ro" = 1 ]; then mount -o remount,ro /; restore_ro=0; fi
fi
sh /data/opt/kas-command-router/rollback.sh
