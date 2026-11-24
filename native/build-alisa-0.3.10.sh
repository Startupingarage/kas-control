#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo 'Usage: sh native/build-alisa-0.3.10.sh ALISA_CHECKOUT INPUT_DIRECTORY' >&2
    exit 2
fi
patch=$(cd "$(dirname "$0")" && pwd)/patches/alisa-voice-0.3.10.patch
inputs=$(cd "$2" && pwd)
cd "$1"
expected=24806ecaea2258bbaa0c779ebceb9fdfeebc66fb
if [ "$(git rev-parse HEAD)" != "$expected" ]; then
    echo "Expected upstream Alisa Voice v0.3.10 ($expected)" >&2
    exit 2
fi

(
    cd "$inputs"
    sha256sum -c <<'HASHES'
955725f3174b5321e25b5ab6760c5d0c5e9f3bbcdb41b386f105f4de9e956757  alisa-voice-sources.tar.gz
24aeeec8af16c381934a6cd7d95c807a8cb2cf7df9fa40d359aa884195c4716c  zig-0.14.1.tar.xz
HASHES
)

if git apply --reverse --check "$patch" 2>/dev/null; then
    echo 'Kas Control patch already applied'
else
    git apply --check "$patch"
    git apply "$patch"
fi

tar -xzf "$inputs/alisa-voice-sources.tar.gz" --strip-components=1
mkdir -p engines/native/toolchains
tar -xJf "$inputs/zig-0.14.1.tar.xz" -C engines/native/toolchains

jobs=${JOBS:-4}
python3 engines/native/scripts/build_native.py --jobs "$jobs"
cmake -S engines/native -B engines/native/build/client -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$PWD/engines/native/build/arm/toolchain.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DALISA_USE_NETEQ=ON
cmake --build engines/native/build/client --target alisa-standalone --parallel "$jobs"

install -m 755 engines/native/build/client/alisa-standalone assets/native/bin/alisa-standalone
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

folder = Path("assets/native")
manifest = folder / "manifest.json"
metadata = json.loads(manifest.read_text(encoding="utf-8"))
name = "bin/alisa-standalone"
metadata["files"][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
print(f"Native package ready. SHA-256 {metadata['files'][name]}")
PY
