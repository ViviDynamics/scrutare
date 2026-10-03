# Preflight commands

Run these from the repository root before proposing an ingestion change.
CI runs every gate on Python 3.10 and 3.14. Tests replace external gh calls
and need neither authentication nor network access.

| Gate | Command | Required result |
| --- | --- | --- |
| Locked environment | `uv sync --locked --extra dev` | Successful sync without changing uv.lock |
| Full tests | `uv run --locked --extra dev pytest` | All tests pass |
| Lint | `uv run --locked --extra dev ruff check .` | No violations |
| Strict types | `uv run --locked --extra dev mypy src` | No errors |
| Wheel | `uv build --wheel` | Wheel created in dist/ |
| Installed CLI | Commands below | Console and module versions work, help works, invalid PR fails |

The installed smoke check runs outside the source checkout:

```sh
uv venv /tmp/scrutare-smoke
uv pip install --python /tmp/scrutare-smoke/bin/python dist/*.whl
cd /tmp
/tmp/scrutare-smoke/bin/scrutare --version
/tmp/scrutare-smoke/bin/python -m scrutare --version
/tmp/scrutare-smoke/bin/scrutare review --help
if /tmp/scrutare-smoke/bin/python -m scrutare review --pr invalid; then
  echo "Expected invalid PR to fail"
  exit 1
fi
```

CI is defined in [.github/workflows/ci.yml](../.github/workflows/ci.yml).
No check may be skipped or converted to an allowed failure.
