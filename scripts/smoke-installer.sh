#!/usr/bin/env bash
# Serve the candidate assets in place of their future release URLs, then follow the setup prompt.
set -euo pipefail
tag=${1:?usage: smoke-installer.sh RELEASE_TAG}
. ./nare.pin
assets="$PWD/dist"
sandbox=$(mktemp -d)
trap 'rm -rf "$sandbox"' EXIT
export XDG_DATA_HOME="$sandbox/user data"
export SCRUTARE_SMOKE_CURL
SCRUTARE_SMOKE_CURL=$(command -v curl)
export SCRUTARE_SMOKE_ASSETS="$assets" SCRUTARE_SMOKE_TAG="$tag"
mkdir -p "$sandbox/path" "$sandbox/checkout"
cat > "$sandbox/path/curl" <<'CURL'
#!/bin/sh
set -eu
base=https://github.com/ViviDynamics/scrutare/releases
wheel=scrutare-$SCRUTARE_SMOKE_TAG-py3-none-any.whl
# uv's private bootstrap also uses curl when testing on a host without uv.
case "$*" in
    *github.com/astral-sh/uv/*|*releases.astral.sh/github/uv/*) exec "$SCRUTARE_SMOKE_CURL" "$@" ;;
esac
case "${2:-}" in
    "$base/latest/download/install.sh") file=install.sh ;;
    "$base/download/$SCRUTARE_SMOKE_TAG/$wheel") file="$wheel" ;;
    *) echo 'unexpected installer asset request' >&2; exit 1 ;;
esac
flags=$1
shift 2
exec "$SCRUTARE_SMOKE_CURL" "$flags" "file://$SCRUTARE_SMOKE_ASSETS/$file" "$@"
CURL
# The prompt checks existing GitHub and provider access. Keep this fixture read-only.
cat > "$sandbox/path/gh" <<'GH'
#!/bin/sh
test "$*" = 'auth status --hostname github.com'
GH
chmod +x "$sandbox/path/curl" "$sandbox/path/gh"
export OPENAI_API_KEY=offline-fixture
export PATH="$sandbox/path:$PATH"
cd "$sandbox/checkout"
git init -q
cli="$XDG_DATA_HOME/scrutare/bin/scrutare"
pr=https://github.com/owner/repo/pull/12
for pass in fresh repeat; do
    output=$(curl -fsSL https://github.com/ViviDynamics/scrutare/releases/latest/download/install.sh | sh)
    grep -Fx "Executable: $cli" <<< "$output"
    test "$("$cli" --version)" = "scrutare $tag"
    gh auth status --hostname github.com >/dev/null 2>&1
    test -n "${OPENAI_API_KEY:-}"
    version=$("$cli" --version)
    test "uses: ViviDynamics/scrutare@${version#scrutare }" = "uses: ViviDynamics/scrutare@$tag"
    if [ "$pass" = fresh ]; then
        "$cli" setup --pr "$pr" --provider openai --model smoke > next-command
        printf '# local edits\n' >> scrutare.yaml
        cp scrutare.yaml original.yaml
    else
        "$cli" setup --pr "$pr" > repeated-command
        cmp next-command repeated-command
        cmp scrutare.yaml original.yaml
    fi
done
python3 - "$pr" "$NARE_RELEASE" <<'PY'
import json, os, shlex, subprocess, sys
from pathlib import Path
args = shlex.split(Path("next-command").read_text())
assert args[1:8] == ["-m", "scrutare", "review", "--pr", sys.argv[1], "--config", str(Path("scrutare.yaml").absolute())]
assert os.access(args[0], os.X_OK) and args[8] == "--nare-executable"
assert json.loads(subprocess.check_output([args[9], "contract"]))["nare"] == sys.argv[2]
assert not Path(".scrutare").exists()
PY
