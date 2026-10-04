# Preflight commands

Run these from the repository root before proposing an ingestion change.
CI runs every gate on Python 3.10 and 3.14. Tests replace external gh calls
and need neither authentication nor network access. All initial lanes run
for every change, in the same order as CI.

Run the complete table with `SCRUTARE_PYTHON=3.10`, then repeat with
`SCRUTARE_PYTHON=3.14`. This checks both full test suites and all other gates
on the same Python versions as the CI matrix.
Commands default to Python 3.10 when `SCRUTARE_PYTHON` is unset.

| Area | Paths | Command |
| --- | --- | --- |
| Locked environment | always | `uv sync --python "${SCRUTARE_PYTHON:-3.10}" --locked --extra dev` |
| Full tests | always | `uv run --python "${SCRUTARE_PYTHON:-3.10}" --locked --extra dev pytest` |
| Lint | always | `uv run --python "${SCRUTARE_PYTHON:-3.10}" --locked --extra dev ruff check .` |
| Strict types | always | `uv run --python "${SCRUTARE_PYTHON:-3.10}" --locked --extra dev mypy src` |
| Wheel | always | `uv build --python "${SCRUTARE_PYTHON:-3.10}" --wheel` |
| Installed CLI | always | `scripts/smoke-cli.sh` |

The installed smoke check uses the selected project Python, installs the built
wheel in a temporary environment outside the checkout, checks the console and
module versions and help, and requires invalid PR input to fail. It removes
the temporary environment when finished.

CI is defined in [.github/workflows/ci.yml](../.github/workflows/ci.yml).
No check may be skipped or converted to an allowed failure.
