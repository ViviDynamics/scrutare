#!/bin/sh
# Assemble the release asset after check-release.py has validated the wheel and tag.
set -eu
tag=${1:?usage: build-installer.sh RELEASE_TAG}
. ./nare.pin
case "$tag" in ''|*[!0-9.]*) echo 'invalid release tag' >&2; exit 1;; esac
test -f "dist/scrutare-$tag-py3-none-any.whl"
{
    printf '#!/bin/sh\nset -eu\nrelease=%s\n' "$tag"
    printf 'nare_release=%s\nnare_commit=%s\n' "$NARE_RELEASE" "$NARE_COMMIT"
    cat scripts/install.sh.in
} > dist/install.sh
