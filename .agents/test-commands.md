# Preflight commands

Run these from the repository root before proposing a change.
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
| Strict types | always | `uv run --python "${SCRUTARE_PYTHON:-3.10}" --locked --extra dev mypy src scripts/check-release.py` |
| Wheel | always | `uv build --python "${SCRUTARE_PYTHON:-3.10}" --wheel` |
| Installed CLI | always | `scripts/smoke-cli.sh` |

Set `SCRUTARE_TEST_NARE_EXECUTABLE` to the absolute separately installed nare
2026.10.4 console path before the complete table. See
[docs/session-fanout.md](../docs/session-fanout.md) for the exact release commit
and locked installation. The full acceptance run requires these installed
runtime cases to execute, with no skips. Keep lanes sequential because `uv sync`
replaces the shared project environment.

The installed smoke check uses the selected project Python, installs the built
wheel in a temporary environment outside the checkout, checks the console and
module versions and help, and requires the exact invalid-PR refusal with empty
success stdout and no run artifacts. It needs the selected nare setting or a usable nare on PATH. It removes
the temporary environment when finished.

CI is defined in [.github/workflows/ci.yml](../.github/workflows/ci.yml).
No check may be skipped or converted to an allowed failure.
