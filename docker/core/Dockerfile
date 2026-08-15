FROM debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 AS fetch

ARG TARGETARCH
ARG RAVENCOIN_VERSION=4.8.0
ARG RAVENCOIN_SOURCE_COMMIT=b60f50e04f1fba425b28804e61be2694faaf3469
ARG RAVENCOIN_ARCHIVE_SHA256=966cf8978af1f2e3f36e9733d011eb92f4116750af6f8e77c5a5ced525577c4c
ARG RAVEND_SHA256=13e5c5da527888a870554acdbe0ad3de4f882ee2102d1cfc521e2868fa17b17b
ARG RAVEN_CLI_SHA256=fcc7b88bd456e9ef21d9d5e9c9b40b26f0276b371f2108d702c6ce242bfdb109

RUN test "$TARGETARCH" = amd64

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/ravencoin
RUN archive="ravencoin-${RAVENCOIN_VERSION}-x86_64-linux-gnu.tar.gz" \
    && curl --fail --location --proto '=https' --tlsv1.2 \
       --output "$archive" \
       "https://github.com/2miners/Ravencoin/releases/download/v${RAVENCOIN_VERSION}/${archive}" \
    && printf '%s  %s\n' "$RAVENCOIN_ARCHIVE_SHA256" "$archive" | sha256sum --check --strict \
    && tar --extract --gzip --file "$archive" \
    && cd "ravencoin-${RAVENCOIN_VERSION}-x86_64-linux-gnu/bin" \
    && printf '%s  ravend\n%s  raven-cli\n' "$RAVEND_SHA256" "$RAVEN_CLI_SHA256" \
       | sha256sum --check --strict

FROM debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

ARG RAVENCOIN_VERSION=4.8.0
ARG RAVENCOIN_SOURCE_COMMIT=b60f50e04f1fba425b28804e61be2694faaf3469

LABEL org.opencontainers.image.title="Ravencoin Core" \
      org.opencontainers.image.version="${RAVENCOIN_VERSION}" \
      org.opencontainers.image.source="https://github.com/2miners/Ravencoin" \
      org.opencontainers.image.revision="${RAVENCOIN_SOURCE_COMMIT}"

RUN groupadd --gid 10001 ravencoin \
    && useradd --uid 10001 --gid 10001 --system \
       --home-dir /var/lib/ravencoin --shell /usr/sbin/nologin ravencoin \
    && install -d -o ravencoin -g ravencoin \
       /var/lib/ravencoin /var/lib/ravencoin-config

COPY --from=fetch \
    /tmp/ravencoin/ravencoin-4.8.0-x86_64-linux-gnu/bin/ravend \
    /tmp/ravencoin/ravencoin-4.8.0-x86_64-linux-gnu/bin/raven-cli \
    /usr/local/bin/
COPY --chmod=0555 docker/core/entrypoint.sh /usr/local/bin/ravencoin-entrypoint
COPY --chmod=0555 docker/core/healthcheck.sh /usr/local/bin/ravencoin-healthcheck

USER ravencoin
WORKDIR /var/lib/ravencoin
VOLUME ["/var/lib/ravencoin", "/var/lib/ravencoin-config"]
EXPOSE 8767
STOPSIGNAL SIGTERM
ENTRYPOINT ["ravencoin-entrypoint"]
CMD []
