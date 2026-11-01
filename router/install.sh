#!/bin/sh
# Run from the uploaded router bundle directory; no changes to voice startup/runtime.
set -eu
umask 077
B=/data/opt/kas-command-router
HOOK=/etc/init.d/S99zkasroutes
resume=false
case "${1:-}" in '') :;; --resume) resume=true;; *) echo 'Usage: install.sh [--resume]' >&2; exit 1;; esac
case "$(uname -m)" in aarch64) :;; *) echo 'Expected aarch64 station' >&2; exit 1;; esac
[ -d /etc/init.d ] && [ "$(readlink -f /etc/init.d)" = /etc/init.d ] || { echo 'Unexpected boot layout; inspect before installation' >&2; exit 1; }
[ -f router-arm64 ] && [ -f supervise.sh ] && [ -f rollback.sh ]
mkdir -p "$B"
if [ -f "$B/router" ] && [ "$resume" = false ]; then
  echo 'Existing install: stop/backup explicitly before replacing router' >&2
  exit 1
fi
[ ! -e "$HOOK" ] || { echo 'Boot hook already exists' >&2; exit 1; }
if [ "$resume" = true ]; then
  cmp router-arm64 "$B/router"
  cmp supervise.sh "$B/supervise.sh"
  cmp rollback.sh "$B/rollback.sh"
  [ "$(tr -d ' \t\r\n' < "$B/routes.json")" = '{"version":1,"routes":[]}' ] || { echo 'Resume requires empty routes' >&2; exit 1; }
  for file in commands.original supervisor.pid status.json router.log; do
    [ ! -e "$B/$file" ] || { echo 'Resume requires an unstarted partial installation' >&2; exit 1; }
  done
else
  cp router-arm64 "$B/router"
  cp supervise.sh rollback.sh "$B/"
fi
chmod 700 "$B/router" "$B/supervise.sh" "$B/rollback.sh"
if [ ! -f "$B/routes.json" ]; then printf '%s\n' '{"version":1,"routes":[]}' > "$B/routes.json"; fi
"$B/router" validate
root_options=$(awk '$2=="/" {print $4}' /proc/mounts)
[ -n "$root_options" ] || { echo 'Cannot determine root mount options' >&2; exit 1; }
restore_ro=false
cleanup() {
  code=$?
  trap - EXIT INT TERM HUP
  if [ "$restore_ro" = true ]; then
    rm -f "$HOOK.tmp"
    mount -o remount,ro / || { echo 'Failed to restore read-only root; restore it manually' >&2; exit 1; }
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
case ",$root_options," in
  *,ro,*) restore_ro=true; mount -o remount,rw /;;
  *,rw,*) :;;
  *) echo 'Unknown root mount mode' >&2; exit 1;;
esac
cat > "$HOOK.tmp" <<'BOOT'
#!/bin/sh
case "${1:-start}" in
 start) nohup /data/opt/kas-command-router/supervise.sh >>/data/opt/kas-command-router/router.log 2>&1 </dev/null & ;;
 stop) /data/opt/kas-command-router/rollback.sh ;;
esac
BOOT
chmod 700 "$HOOK.tmp"
mv "$HOOK.tmp" "$HOOK"
if [ "$restore_ro" = true ]; then
  mount -o remount,ro /
  restore_ro=false
fi
trap - EXIT INT TERM HUP
"$HOOK" start
echo 'Router started. Require fresh status.json ready=true before adding routes.'
