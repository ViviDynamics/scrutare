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
its hosted on-prem host attempt failed with HTTP 524. The published `2026.10.0` has a
GitHub release wheel and public GHCR image, but no reusable Action. Installation
requires matching tag, wheel and image assets in
[GitHub releases](https://github.com/ViviDynamics/scrutare/releases) and GHCR.
Asset publication alone does not establish hosted acceptance. There is no PyPI
installation claim. See the [pipeline guide](pipeline.md) for the pinned caller,
trusted configuration and prerequisites.

## Wheel

Scrutare supports Python 3.10 and newer. Published wheels are immutable GitHub
release assets; use the installation instructions at the matching Scrutare tag
for its compatible Nare runtime. Releases through 2026.10.47 use older Nare pins
and cannot use this checkout's contract 3 integration.

For contract 3 support, install this reviewed source checkout until a release
containing the integration is published:

```sh
uv tool install --python 3.10 .
scrutare --version
```

Install gh separately and use already authorized GitHub access. Install nare
2026.11.107 in a separate Python 3.14 environment with its own locked dependencies:

```sh
git clone --depth 1 --branch 2026.11.107 https://github.com/ViviDynamics/nare.git nare-2026.11.107
test "$(git -C nare-2026.11.107 rev-parse HEAD)" = 4baaeb8527478401cbd7bf54b74f48c286d8a093
uv sync --project nare-2026.11.107 --python 3.14 --locked --no-dev
scrutare review --pr https://github.com/owner/repo/pull/12 \
  --nare-executable "$PWD/nare-2026.11.107/.venv/bin/nare"
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

## Nare pin

`nare.pin` at the repository root is the one record of the nare release every
installation uses: its release tag and commit as `NARE_RELEASE` and `NARE_COMMIT`.
The Dockerfile copies it into the builder stage, the CI and release workflows and
`scripts/smoke-container.sh` source it, and the installed tests read the expected
version from it. Every install clones the tag and refuses a checkout whose commit
differs. The documents above keep literal versions so they can be copied; the bump
script rewrites them.

`scripts/bump-nare.py` moves the pin:

```sh
python3 scripts/bump-nare.py status                # pin, latest release, changed
python3 scripts/bump-nare.py bump latest --dry-run # report and diff, no file written
python3 scripts/bump-nare.py bump 2026.11.200 \
  --old-contract old-contract.json --new-contract new-contract.json
```

It reads nare's published releases through `gh api` (drafts and prereleases are
skipped), verifies the target tag's commit with `git ls-remote`, rewrites
`nare.pin`, the literal versions in the user documents and the previous pin in
`tests/test_nare_runtime_pin.py`, and prints the release notes in the range. Given
both releases' `nare contract` output it also lists the contract version, added,
removed and changed flags and entries, and every changed default. A pin that is
already current is a no-op; an older target, an unpublished release, a missing
tag or a tag that no longer names the pinned commit is refused before anything is
written. A document that stops naming the pin also stops the bump, so the list of
rewritten documents cannot go stale silently.

The Nare bump workflow runs this daily and on dispatch (optionally for a named
release). When the pin differs from nare's latest release it records both
releases' `nare contract`, rebuilds the fixed branch `automation/nare-bump` from
main with one bump commit, and opens or refreshes the single pull request from
that branch with the report as its body. A contract version change is called out
at the top of the body and labeled `nare-contract-change`. A run with nothing new
leaves the branch and its pull request alone, and a branch that carries a
maintainer's commit is never overwritten. The workflow never merges: a green,
additive bump is merged with `merge-verified` after review.

The workflow uses only the default `GITHUB_TOKEN`, with `contents: write` (the
branch), `pull-requests: write` (the pull request and its label) and
`actions: write`. A push or pull request made with `GITHUB_TOKEN` starts no other
workflow, so the third permission lets it dispatch CI and the disclosure guard on
the bump branch itself, the same way Auto-tag dispatches Release. Their runs report
on the bump commit and show on the pull request. No maintainer token or secret is
needed. A maintainer push to the branch starts the usual push CI.

Policy: a nare defect found by a bump is filed on
[ViviDynamics/nare](https://github.com/ViviDynamics/nare/issues) with a minimal
reproduction, linked from the bump pull request, and fixed in nare. Scrutare does
not carry workarounds for nare defects; the bump waits for the fixed release.

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

The Docker build context allows only its recipe, ignore policy, `nare.pin` and wheel.
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
