# Release installation and packaging

Published [Scrutare 2026.10.2](https://github.com/ViviDynamics/scrutare/releases/tag/2026.10.2)
adds gated OpenAI streaming and updates the fixed Action/runtime pins. Its
public wheel and GHCR image are verified at commit
`0e48f9904a398fe33520c85d44bcf73f3f0c8723`. Both
[focused hosted success](https://github.com/ViviDynamics/scrutare/actions/runs/37459891906)
and [invalid-config hosted failure](https://github.com/ViviDynamics/scrutare/actions/runs/37462193493)
are recorded in the [dated acceptance record](action-acceptance.md), including
asset digests and evidence limits. This does not announce issue 14 or M1 closure.

The published `2026.10.1` includes the reusable Action and remains immutable;
its hosted Spark attempt failed with HTTP 524. The published `2026.10.0` has a
GitHub release wheel and public GHCR image, but no reusable Action. Installation
requires matching tag, wheel and image assets in
[GitHub releases](https://github.com/ViviDynamics/scrutare/releases) and GHCR.
Asset publication alone does not establish hosted acceptance. There is no PyPI
installation claim. See the [pipeline guide](pipeline.md) for the pinned caller,
trusted configuration and prerequisites.

## Wheel

Scrutare supports Python 3.10 and newer. Install the published wheel:

```sh
uv tool install --python 3.10 \
  https://github.com/ViviDynamics/scrutare/releases/download/2026.10.2/scrutare-2026.10.2-py3-none-any.whl
scrutare --version
```

Install gh separately and use already authorized GitHub access. Install nare
2026.10.4 in a separate Python 3.14 environment with its own locked dependencies:

```sh
git clone --depth 1 --branch 2026.10.4 https://github.com/ViviDynamics/nare.git nare-2026.10.4
test "$(git -C nare-2026.10.4 rev-parse HEAD)" = 79405f9d2e3db2efe4a3ffda35faaf1680000acd
uv sync --project nare-2026.10.4 --python 3.14 --locked --no-dev
scrutare review --pr https://github.com/owner/repo/pull/12 \
  --nare-executable "$PWD/nare-2026.10.4/.venv/bin/nare"
```

Prepare `scrutare.yaml` and authorized provider credentials before review. The
nare interpreter is independent of Scrutare's interpreter; nare is not a Python
dependency of the wheel. See [CLI usage](cli.md) and [session fan-out](session-fanout.md).

To build and install locally from a reviewed checkout:

```sh
uv sync --python 3.14 --locked --extra dev
uv build --wheel
uv tool install --python 3.14 ./dist/scrutare-2026.10.2-py3-none-any.whl
```

## Container

The image includes Scrutare, gh 2.100.0, git and the separately pinned nare
runtime. It starts directly with `scrutare`, works in `/work` and defaults to
UID/GID 10001. Use the published image:

```sh
docker run --rm ghcr.io/vividynamics/scrutare:2026.10.2 --version
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e GH_TOKEN -e ANTHROPIC_API_KEY -v "$PWD:/work" \
  ghcr.io/vividynamics/scrutare:2026.10.2 review --pr 12
```

The second command explicitly forwards credentials already present for an
authorized run; choose the provider variable matching the configuration. Numeric
PRs need a mounted git checkout. Mount a directory writable by the runtime user
so run artifacts survive container removal. The example selects your host UID
and GID; otherwise provision the mount for UID/GID 10001. Credentials are not
baked into the image. For a local image, use the same wheel:

```sh
docker buildx build --builder default --load \
  --build-arg SCRUTARE_WHEEL=dist/scrutare-2026.10.2-py3-none-any.whl \
  --tag scrutare:2026.10.2 .
scripts/smoke-container.sh scrutare:2026.10.2 2026.10.2
```

The smoke runs with networking disabled and no host credential forwarding.
It verifies the installed package, personas, gh features, nare contract, git
repository discovery and writable non-root working directory. It does not
perform a live PR review.

## Release contract

The development version is `src/scrutare/__init__.py`. Hatch reads it into wheel
metadata. A release stamps that file from its tag in the build checkout only;
if `uv.lock` carries a project version, that entry is updated too. The current
dynamic editable entry requires no lockfile change. Automation never pushes a
version-bump commit.
Unprefixed CalVer tags use `YYYY.M.N`; stamped source, wheel metadata, wheel
filename, CLI and image versions must agree. Producer envelopes identify this
version without changing raw capture/config bytes, bare findings arrays or
canonical schema-1 Verdict bytes.

After successful push CI on this repository's main, Auto-tag tags the exact
validated commit and explicitly dispatches Release on that tag. This follows
[qare's auto-tag flow](https://github.com/ViviDynamics/qare/blob/main/.github/workflows/auto-tag.yml)
and [conductor's tag-derived versions](https://github.com/ViviDynamics/conductor/blob/main/.github/workflows/main-branch-build.yml).
PR CI, failed CI and other branches cannot start automatic publication. A manual
Auto-tag dispatch must target main and have successful push CI for that exact
commit. Publication uses `GITHUB_TOKEN`; no additional release secret is needed.

An existing canonical release tag on the commit is reused. Otherwise an untagged
development version is used, matching qare; if it is already tagged, the next
patch is derived from main's release history and the UTC build month. A new
month starts at zero. Nonrelease tags do not suppress releases, and tags outside
main cannot reset the counter, though their names are reserved to avoid collisions.

The release workflow accepts tag pushes and explicit dispatches on tags only.
It requires the tagged commit to be on main, runs the unchanged source's tests,
lint and strict types on Python 3.10 and 3.14, builds and validates one wheel,
installs and smokes it outside the checkout, then builds and smokes that exact
wheel's image. Only then does it create the GitHub release with the wheel and
push the versioned GHCR image. There is no `latest` alias. Publication uses
narrowly scoped workflow permissions. The composite Action resolves its immutable
image from its canonical release tag, so `ViviDynamics/scrutare@YYYY.M.N` runs
the same release's image and validates that version in its result. Branch and
SHA Action references refuse; existing historical release tags remain unchanged.
A failure during
publication can leave the wheel release present before image publication;
inspect both assets before announcing the release.

Auto-tag and Release each serialize their runs. GitHub keeps one running and one
pending run per concurrency group; a burst can supersede a pending run. A merge
can therefore ship as part of a later release, and a superseded release tag can
be recovered by rerunning its Auto-tag run. Auto-tag skips tags with an existing
release run and re-dispatches when no run started or all runs were cancelled or
superseded. For a failed release, rerun Release itself on the same tag; existing
wheel assets are downloaded, validated and reused for the installed/container
smokes and image build; missing assets are uploaded. Dispatch is checked
for an actual run, and a missing run fails visibly rather than silently stranding
the tag.

The Docker build context allows only its recipe, ignore policy and wheel.
Source checkouts, virtual environments, run artifacts, repository state and
credentials stay outside the context. Local verification covers native amd64;
it does not establish an arm64 build or remote publication.

The full local acceptance commands are in
[.agents/test-commands.md](../.agents/test-commands.md). Run both lanes in order
with the selected actual nare executable, native Docker and no skips. The mandatory
Action tests build the exact candidate wheel into the production Dockerfile and
run the adapter against that installed image with networking disabled. They
verify real nare execution, accounting, canonical bytes, explicit UID/GID and
hidden evidence archive readability. Installed pipeline tests
substitute only the provider factory and GitHub transport boundary; real model
access, a literal live PR review, and published asset availability remain
separate acceptance evidence.
