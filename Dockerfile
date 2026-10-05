FROM python:3.14-slim-bookworm AS runtime

FROM runtime AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git curl \
    && rm -rf /var/lib/apt/lists/*
# SHA-256 values from the public cli/cli v2.100.0 release checksum manifest.
RUN arch=$(dpkg --print-architecture) \
    && case "$arch" in \
         amd64) checksum=e4d4bb4498e8d007abe545b6568926793ace1b6447da598294a610018cb164be ;; \
         arm64) checksum=ea4e7a581a32ccad6cc7923cb1576ac5859ba4b9a16ab22eb8f8a96e78e2e961 ;; \
         *) echo "Unsupported gh architecture: $arch" >&2; exit 1 ;; \
       esac \
    && curl --fail --location --silent --show-error \
       "https://github.com/cli/cli/releases/download/v2.100.0/gh_2.100.0_linux_${arch}.tar.gz" \
       --output /tmp/gh.tar.gz \
    && echo "$checksum  /tmp/gh.tar.gz" | sha256sum --check \
    && tar -xzf /tmp/gh.tar.gz --strip-components=2 -C /usr/local/bin \
       "gh_2.100.0_linux_${arch}/bin/gh"
# Public tagged source, verified before installation; no build credentials.
RUN git clone --depth 1 --branch 2026.10.4 https://github.com/ViviDynamics/nare.git /tmp/nare \
    && test "$(git -C /tmp/nare rev-parse HEAD)" = "79405f9d2e3db2efe4a3ffda35faaf1680000acd" \
    && UV_PROJECT_ENVIRONMENT=/opt/nare UV_PYTHON_DOWNLOADS=never \
       uv sync --project /tmp/nare --python /usr/local/bin/python --locked --no-dev --no-editable \
    && test "$(/opt/nare/bin/nare --version)" = "2026.10.4"
ARG SCRUTARE_WHEEL
COPY ${SCRUTARE_WHEEL} /tmp/wheels/
RUN test -n "$SCRUTARE_WHEEL" \
    && test "$(find /tmp/wheels -maxdepth 1 -name '*.whl' | wc -l)" -eq 1 \
    && uv venv --python /usr/local/bin/python /opt/scrutare \
    && uv pip install --python /opt/scrutare/bin/python /tmp/wheels/*.whl

FROM runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 scrutare \
    && mkdir /work && chown scrutare:scrutare /work
COPY --from=builder /usr/local/bin/gh /usr/local/bin/gh
COPY --from=builder /opt/nare /opt/nare
COPY --from=builder /opt/scrutare /opt/scrutare
ENV PATH="/opt/scrutare/bin:/opt/nare/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["scrutare"]
