#!/bin/sh
set -eu
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT INT TERM
mkdir -p "$T/bundle" "$T/bin" "$T/init" "$T/partial"
printf 'root / ubifs ro,relatime 0 0\n' > "$T/mounts"
cat > "$T/bin/uname" <<'SH'
#!/bin/sh
echo aarch64
SH
cat > "$T/bin/mount" <<SH
#!/bin/sh
echo "\$*" >> "$T/mount.log"
SH
cat > "$T/bin/chmod" <<SH
#!/bin/sh
case "\$*" in *S99zkasroutes.tmp*) [ ! -e "$T/fail" ] || exit 1;; esac
/bin/chmod "\$@"
SH
chmod +x "$T/bin/"*
printf '#!/bin/sh\nexit 0\n' > "$T/bundle/router-arm64"
printf '#!/bin/sh\nexit 0\n' > "$T/bundle/supervise.sh"
cp "$T/bundle/supervise.sh" "$T/bundle/rollback.sh"
sed -e "s|/data/opt/kas-command-router|$T/partial|g" -e "s|/etc/init.d|$T/init|g" -e "s|/proc/mounts|$T/mounts|g" install.sh > "$T/bundle/install.sh"
cd "$T/bundle"
export PATH="$T/bin:$PATH"
touch "$T/fail"
if sh install.sh >/dev/null 2>&1; then echo 'Expected write failure' >&2; exit 1; fi
[ "$(tail -n 1 "$T/mount.log")" = '-o remount,ro /' ]
[ ! -e "$T/init/S99zkasroutes" ]
rm "$T/fail"
if sh install.sh >/dev/null 2>&1; then echo 'Existing partial accepted without resume' >&2; exit 1; fi
cp supervise.sh supervise.saved
printf changed >> supervise.sh
if sh install.sh --resume >/dev/null 2>&1; then echo 'Mismatched resume accepted' >&2; exit 1; fi
mv supervise.saved supervise.sh
sh install.sh --resume >/dev/null
[ -x "$T/init/S99zkasroutes" ]
[ "$(tail -n 1 "$T/mount.log")" = '-o remount,ro /' ]
echo 'Installer partial resume, hash mismatch and RO restoration checks passed.'
