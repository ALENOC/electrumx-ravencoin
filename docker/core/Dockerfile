# Bullseye keeps the upstream secp256k1 OpenSSL test suite compatible while
# preserving the exact candidate-source build path for ARM64.
FROM debian:bullseye-slim@sha256:f313b4bd62667092a59b3a664d7d3ab8b5e65f41675f48e81455a15dc5abe792 AS fetch

ARG TARGETARCH
ARG RAVENCOIN_VERSION=4.8.0
ARG RAVENCOIN_SOURCE_COMMIT=22549129888d02e0e08fcdb9f96f3c699167e774
ARG RAVENCOIN_ARCHIVE_SHA256=cb359b6a5b42e47068cd655231484fcc763d2f79eae5ea318b029c704a4dc020
ARG RAVENCOIN_SOURCE_ARCHIVE_SHA256=0fba4c8979c7ebed3457d91957f886292ea53e62857c636c70e3aa0478017d6e
ARG RAVEND_SHA256=885f6670c819e3a48339bbc596f1a224fe41af21ae7a0db57b2ebca700d050ea
ARG RAVEN_CLI_SHA256=8fde2465b2bd50d0db2d873fd02a6a4ce6a6be84ed03d32b094a9862febb594b

RUN test "$TARGETARCH" = amd64 || test "$TARGETARCH" = arm64

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       autoconf automake bsdextrautils build-essential ca-certificates curl \
       libboost-all-dev libdb++-dev libevent-dev libssl-dev libtool pkg-config \
       python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/ravencoin
RUN set -eux; \
    mkdir -p /out; \
    if [ "$TARGETARCH" = amd64 ]; then \
        # The RavenProject v4.8.0 asset name embeds a CI build number
        # (225491298) that is not derivable from RAVENCOIN_VERSION; it is
        # pinned here alongside the version and re-verified by checksum.
        archive="raven-${RAVENCOIN_VERSION}-225491298-x86_64-linux-gnu.tar.gz"; \
        curl --fail --location --proto '=https' --tlsv1.2 \
           --output "$archive" \
           "https://github.com/RavenProject/Ravencoin/releases/download/v${RAVENCOIN_VERSION}/${archive}"; \
        printf '%s  %s\n' "$RAVENCOIN_ARCHIVE_SHA256" "$archive" \
           | sha256sum --check --strict; \
        tar --extract --gzip --file "$archive"; \
        cd "raven-${RAVENCOIN_VERSION}-225491298/bin"; \
        printf '%s  ravend\n%s  raven-cli\n' "$RAVEND_SHA256" "$RAVEN_CLI_SHA256" \
           | sha256sum --check --strict; \
        install -Dm0555 ravend raven-cli -t /out; \
    else \
        archive="ravencoin-${RAVENCOIN_SOURCE_COMMIT}.tar.gz"; \
        curl --fail --location --proto '=https' --tlsv1.2 \
           --output "$archive" \
           "https://github.com/RavenProject/Ravencoin/archive/${RAVENCOIN_SOURCE_COMMIT}.tar.gz"; \
        printf '%s  %s\n' "$RAVENCOIN_SOURCE_ARCHIVE_SHA256" "$archive" \
           | sha256sum --check --strict; \
        mkdir source; tar --extract --gzip --file "$archive" --strip-components=1 \
           --directory source; \
        cd source; \
        ./autogen.sh; \
        ./configure --disable-wallet --without-gui --without-miniupnpc \
           --disable-bench; \
        make -j"$(nproc)"; \
        make check; \
        install -Dm0555 src/ravend src/raven-cli -t /out; \
    fi

FROM debian:bullseye-slim@sha256:f313b4bd62667092a59b3a664d7d3ab8b5e65f41675f48e81455a15dc5abe792

ARG RAVENCOIN_VERSION=4.8.0
ARG RAVENCOIN_SOURCE_COMMIT=22549129888d02e0e08fcdb9f96f3c699167e774
ARG TARGETARCH

LABEL org.opencontainers.image.title="Ravencoin Core" \
      org.opencontainers.image.version="${RAVENCOIN_VERSION}" \
      org.opencontainers.image.source="https://github.com/RavenProject/Ravencoin" \
      org.opencontainers.image.revision="${RAVENCOIN_SOURCE_COMMIT}" \
      org.opencontainers.image.architecture="${TARGETARCH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       libboost-chrono1.74.0 libboost-date-time1.74.0 \
       libboost-filesystem1.74.0 libboost-iostreams1.74.0 \
       libboost-program-options1.74.0 libboost-serialization1.74.0 \
       libboost-system1.74.0 libboost-thread1.74.0 \
       libdb5.3 libdb5.3++ libevent-2.1-7 libevent-pthreads-2.1-7 libssl1.1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 ravencoin \
    && useradd --uid 10001 --gid 10001 --system \
       --home-dir /var/lib/ravencoin --shell /usr/sbin/nologin ravencoin \
    && install -d -o ravencoin -g ravencoin \
       /var/lib/ravencoin /var/lib/ravencoin-config

COPY --from=fetch \
    /out/ravend \
    /out/raven-cli \
    /usr/local/bin/
COPY --chmod=0555 docker/core/entrypoint.sh /usr/local/bin/ravencoin-entrypoint
COPY --chmod=0555 docker/core/healthcheck.sh /usr/local/bin/ravencoin-healthcheck
COPY --chmod=0555 docker/core/bootstrap-reindex.sh /usr/local/bin/ravencoin-bootstrap-reindex

USER ravencoin
WORKDIR /var/lib/ravencoin
VOLUME ["/var/lib/ravencoin", "/var/lib/ravencoin-config"]
EXPOSE 8767
STOPSIGNAL SIGTERM
ENTRYPOINT ["ravencoin-entrypoint"]
CMD []
