# Preflight commands

Run these from the repository root before proposing an ingestion change.
CI runs every gate on Python 3.10 and 3.14. Tests replace external gh calls
and need neither authentication nor network access. All initial lanes run
for every change, in the same order as CI.

| Area | Paths | Command |
| --- | --- | --- |
| Locked environment | always | `uv sync --locked --extra dev` |
| Full tests | always | `uv run --locked --extra dev pytest` |
| Lint | always | `uv run --locked --extra dev ruff check .` |
| Strict types | always | `uv run --locked --extra dev mypy src` |
| Wheel | always | `uv build --wheel` |
| Installed CLI | always | `scripts/smoke-cli.sh` |

The installed smoke check uses the selected project Python, installs the built
wheel in a temporary environment outside the checkout, checks the console and
module versions and help, and requires invalid PR input to fail. It removes
the temporary environment when finished.

CI is defined in [.github/workflows/ci.yml](../.github/workflows/ci.yml).
No check may be skipped or converted to an allowed failure.
