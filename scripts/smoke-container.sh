#!/usr/bin/env bash
# Exercise only local, offline runtime capabilities. Never forward host credentials.
set -euo pipefail
image=${1:?usage: smoke-container.sh IMAGE VERSION}
expected=${2:?usage: smoke-container.sh IMAGE VERSION}

docker image inspect --format 'Image identity: {{.Id}}' "$image"
test "$(docker run --rm --network none "$image" --version)" = "scrutare $expected"
test "$(docker run --rm --network none --entrypoint python "$image" -m scrutare --version)" = "scrutare $expected"
echo "Console and module versions: scrutare $expected"
docker run --rm --network none "$image" --help
docker run --rm --network none "$image" review --help
docker run --rm --network none --entrypoint python "$image" -m scrutare --help
docker run --rm --network none --entrypoint gh "$image" --version
docker run --rm --network none --entrypoint nare "$image" --version
docker run --rm --network none -i --entrypoint python "$image" - "$expected" <<'PY'
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

from scrutare import __version__
from scrutare.personas import load_persona

assert importlib.metadata.version("scrutare") == __version__ == sys.argv[1]
assert os.getuid() != 0
assert Path.cwd() == Path("/work")
run = Path(".scrutare/smoke")
run.mkdir(parents=True)
(run / "writable").write_text("local smoke\n")
for name in ("senior-dev", "junior-dev", "security", "devops"):
    assert load_persona(name).system_prompt.strip()
# Capture requires gh pagination to return an array of pages.
assert "--slurp" in subprocess.check_output(["gh", "api", "--help"], text=True)
version = subprocess.check_output(["nare", "--version"], text=True).strip()
assert version == "2026.10.4"
contract = json.loads(subprocess.check_output(["nare", "contract"], text=True))
assert contract["contract"] == 1 and contract["nare"] == version
print(f"UID {os.getuid()}: writable /work, four personas, nare {version}, contract 1")
PY
