#!/usr/bin/env bash
# Verify the built wheel outside the checkout with the selected project Python.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
smoke_root=$(mktemp -d)
trap 'rm -rf "$smoke_root"' EXIT

uv venv --python "$repo_root/.venv/bin/python" "$smoke_root/venv"
uv pip install --python "$smoke_root/venv/bin/python" "$repo_root"/dist/*.whl
cd "$smoke_root"
"$smoke_root/venv/bin/python" - <<'PY'
from scrutare.personas import PersonaDefinition, load_persona, resolve_personas

names = ("senior-dev", "junior-dev", "security", "devops")
entries = resolve_personas(names)
assert tuple(entry.name for entry in entries) == names
assert all(isinstance(entry, PersonaDefinition) for entry in entries)
assert len({entry.system_prompt for entry in entries}) == 4
for name in names:
    assert load_persona(name).system_prompt.strip()
print("Installed persona registry: four packaged prompts loaded")
PY
"$smoke_root/venv/bin/scrutare" --version
"$smoke_root/venv/bin/python" -m scrutare --version
"$smoke_root/venv/bin/scrutare" review --help
cat > scrutare.yaml <<'YAML'
models:
  default:
    model: smoke-test-model
YAML
if "$smoke_root/venv/bin/python" -m scrutare review --pr invalid; then
  echo "Expected invalid PR to fail" >&2
  exit 1
fi
