#!/bin/sh
set -eu
T=$(mktemp -d)
supervisor=
cleanup() { if [ -n "$supervisor" ]; then kill "$supervisor" 2>/dev/null || :; wait "$supervisor" 2>/dev/null || :; fi; rm -rf "$T"; }
trap cleanup EXIT INT TERM
mkdir "$T/router" "$T/voice"
printf '1\topen\n3\tclose\n' > "$T/voice/commands.txt"
cp "$T/voice/commands.txt" "$T/expected"
sed -e "s|/data/opt/kas-command-router|$T/router|g" -e "s|/data/opt/alisa-standalone|$T/voice|g" -e "s|/tmp/kas-command-router-supervisor.lock|$T/supervisor.lock|g" -e "s|/tmp/kas-control-switch.lock|$T/switch.lock|g" supervise.sh > "$T/router/supervise.sh"
cat > "$T/router/router" <<'WORKER'
#!/bin/sh
B=$(dirname "$0")
if [ ! -f "$B/once" ]; then
 touch "$B/once"
 printf 'filtered\n' > "$B/../voice/commands.txt"
 exit 1
fi
trap 'exit 0' TERM INT
while :; do sleep .1; done
WORKER
chmod 700 "$T/router/router"
sh "$T/router/supervise.sh" &
supervisor=$!
n=0
while [ ! -f "$T/router/once" ]; do n=$((n+1)); [ "$n" -lt 30 ]; sleep .1; done
sleep .3
cmp "$T/expected" "$T/voice/commands.txt"
sleep 2
kill "$supervisor"
wait "$supervisor"
supervisor=
cmp "$T/expected" "$T/voice/commands.txt"
echo 'Supervisor worker-death and graceful-stop recovery passed.'
