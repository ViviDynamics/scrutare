# Install Scrutare from its release

Issue #152

## Scope

Publish a tag-stamped shell installer alongside the wheel. Install the Nare runtime specified in `nare.pin` in a separate environment, as the Action does. Provide a copyable setup prompt for the repository that owns the pull request.

## Assumptions

The host has a POSIX shell, curl and Git. Use uv to provision Python 3.14. The user supplies authorized GitHub access and selects a model.

## Tasks

- Exercise the latest-asset one-liner twice in a clean checkout and verify both installed versions.
- Prepare or validate a configuration locally and print a shell-quoted review command for the selected PR.
- Exercise the setup prompt with a new configuration and one the user has edited.
- Document the one-liner and how to pin the Action to the installed release.
