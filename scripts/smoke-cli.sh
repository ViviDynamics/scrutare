#!/usr/bin/env bash
# Verify the built wheel outside the checkout with the selected project Python.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
smoke_root=$(mktemp -d)
trap 'rm -rf "$smoke_root"' EXIT

uv venv --python "$repo_root/.venv/bin/python" "$smoke_root/venv"
uv pip install --python "$smoke_root/venv/bin/python" "$repo_root"/dist/*.whl
cd "$smoke_root"
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
