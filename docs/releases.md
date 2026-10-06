# Release installation and packaging

Scrutare `2026.10.0` is published with a GitHub release wheel and public GHCR
image. It does not include the reusable Action. Scrutare `2026.10.1` includes
that Action. Before using its pinned interfaces, verify the matching GitHub tag,
release wheel and public GHCR image. The commands below require the matching
assets in [GitHub releases](https://github.com/ViviDynamics/scrutare/releases)
and GHCR. Action use also requires literal hosted caller success/failure
acceptance evidence; asset publication alone does not establish hosted
acceptance. There is no PyPI installation claim. See the [pipeline guide](pipeline.md)
for the pinned caller, prerequisites and hosted evidence requirements.

## Wheel

Scrutare supports Python 3.10 and newer. After publication:

```sh
uv tool install --python 3.10 \
  https://github.com/ViviDynamics/scrutare/releases/download/2026.10.1/scrutare-2026.10.1-py3-none-any.whl
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

Before publication, build and install from a reviewed checkout:

```sh
uv sync --python 3.14 --locked --extra dev
uv build --wheel
uv tool install --python 3.14 ./dist/scrutare-2026.10.1-py3-none-any.whl
```

## Container

The image includes Scrutare, gh 2.100.0, git and the separately pinned nare
runtime. It starts directly with `scrutare`, works in `/work` and defaults to
UID/GID 10001. After publication:

```sh
docker run --rm ghcr.io/vividynamics/scrutare:2026.10.1 --version
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e GH_TOKEN -e ANTHROPIC_API_KEY -v "$PWD:/work" \
  ghcr.io/vividynamics/scrutare:2026.10.1 review --pr 12
```

The second command explicitly forwards credentials already present for an
authorized run; choose the provider variable matching the configuration. Numeric
PRs need a mounted git checkout. Mount a directory writable by the runtime user
so run artifacts survive container removal. The example selects your host UID
and GID; otherwise provision the mount for UID/GID 10001. Credentials are not
baked into the image. For a local image before publication, use the same wheel:

```sh
docker buildx build --builder default --load \
  --build-arg SCRUTARE_WHEEL=dist/scrutare-2026.10.1-py3-none-any.whl \
  --tag scrutare:2026.10.1 .
scripts/smoke-container.sh scrutare:2026.10.1 2026.10.1
```

The smoke runs with networking disabled and no host credential forwarding.
It verifies the installed package, personas, gh features, nare contract, git
repository discovery and writable non-root working directory. It does not
perform a live PR review.

## Release contract

The sole authored version is `src/scrutare/__init__.py`. Hatch reads it into wheel
metadata. Unprefixed CalVer tags use `YYYY.M.N`; source, wheel metadata, wheel
filename, CLI and image versions must agree. Producer envelopes identify this
version without changing raw capture/config bytes, bare findings arrays or
canonical schema-1 Verdict bytes.

The release workflow requires the tagged commit to be on main, runs tests,
lint and strict types on Python 3.10 and 3.14, builds and validates one wheel,
installs and smokes it outside the checkout, then builds and smokes that exact
wheel's image. Only then does it create the GitHub release with the wheel and
push the versioned GHCR image. There is no automatic tag creation or `latest`
alias. Publication uses narrowly scoped workflow permissions. A failure during
publication can leave the wheel release present before image publication;
inspect both assets before announcing the release.

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
