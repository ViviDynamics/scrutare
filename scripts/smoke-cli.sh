#!/usr/bin/env bash
# Verify the built wheel outside the checkout with the selected project Python.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
shopt -s nullglob
wheels=("$repo_root"/dist/*.whl)
if (( ${#wheels[@]} != 1 )); then
  echo "Expected exactly one wheel in dist/" >&2
  exit 1
fi
wheel=${wheels[0]}
expected=$("$repo_root/.venv/bin/python" -c 'import runpy, sys; print(runpy.run_path(sys.argv[1])["__version__"])' "$repo_root/src/scrutare/__init__.py")
"$repo_root/.venv/bin/python" "$repo_root/scripts/check-release.py" "$expected" "$wheel"
smoke_root=$(mktemp -d)
trap 'rm -rf "$smoke_root"' EXIT

uv venv --python "$repo_root/.venv/bin/python" "$smoke_root/venv"
uv pip install --python "$smoke_root/venv/bin/python" "$wheel"
cd "$smoke_root"
"$smoke_root/venv/bin/python" - "$expected" <<'PY'
import importlib.metadata
import sys

from scrutare import __version__
from scrutare.personas import PersonaDefinition, load_persona, resolve_personas

assert importlib.metadata.version("scrutare") == __version__ == sys.argv[1]
print(f"Installed metadata and runtime: {__version__}")

names = ("senior-dev", "junior-dev", "security", "devops")
entries = resolve_personas(names)
assert tuple(entry.name for entry in entries) == names
assert all(isinstance(entry, PersonaDefinition) for entry in entries)
assert len({entry.system_prompt for entry in entries}) == 4
for name in names:
    assert load_persona(name).system_prompt.strip()
print("Installed persona registry: four packaged prompts loaded")
PY
test "$("$smoke_root/venv/bin/scrutare" --version)" = "scrutare $expected"
test "$("$smoke_root/venv/bin/python" -m scrutare --version)" = "scrutare $expected"
echo "Console and module versions: scrutare $expected"
"$smoke_root/venv/bin/scrutare" review --help
cat > scrutare.yaml <<'YAML'
models:
  default:
    model: smoke-test-model
YAML
if "$smoke_root/venv/bin/python" -m scrutare review --pr invalid \
    --nare-executable "${SCRUTARE_TEST_NARE_EXECUTABLE:-nare}" \
    > invalid.stdout 2> invalid.stderr; then
  echo "Expected invalid PR to fail" >&2
  exit 1
fi
cat invalid.stderr
test ! -s invalid.stdout
grep -Fx 'scrutare: Use a github.com pull request URL or a positive pull request number.' invalid.stderr
test ! -e .scrutare
