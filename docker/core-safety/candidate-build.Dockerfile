# Reproducible source build of a RavenProject/Ravencoin candidate commit,
# extended with the certification candidate probe suite
# (core-safety/probe/certification_candidate_tests.cpp), for use by
# core-safety/scripts/certify_core.py as --source-dir/--bin-dir/
# --candidate-probe/--candidate-test-binary input.
#
# Generic across candidates: every candidate-identifying value is a build
# ARG with no baked-in default tied to one release. The probe file itself
# calls only long-stable public Ravencoin/Bitcoin-Core-lineage APIs
# (ProcessNewBlockHeaders, ContextualCheckTransferAsset, checkpoint data),
# so it is not expected to need per-release changes; if a future candidate
# renames or removes one of those entry points, the build fails loudly here
# rather than silently certifying without real evidence.
FROM debian:bullseye-slim@sha256:f313b4bd62667092a59b3a664d7d3ab8b5e65f41675f48e81455a15dc5abe792

ARG RAVENCOIN_SOURCE_REPOSITORY=RavenProject/Ravencoin
ARG RAVENCOIN_SOURCE_COMMIT
ARG RAVENCOIN_SOURCE_ARCHIVE_SHA256

RUN test -n "$RAVENCOIN_SOURCE_COMMIT"
RUN test -n "$RAVENCOIN_SOURCE_ARCHIVE_SHA256"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       autoconf automake bsdextrautils build-essential ca-certificates curl \
       libboost-all-dev libdb++-dev libevent-dev libssl-dev libtool pkg-config \
       python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/candidate
COPY core-safety/probe/certification_candidate_tests.cpp \
     core-safety/probe/apply-to-source.sh \
     /tmp/probe/

RUN set -eux; \
    archive="candidate-${RAVENCOIN_SOURCE_COMMIT}.tar.gz"; \
    curl --fail --location --proto '=https' --tlsv1.2 \
       --output "$archive" \
       "https://github.com/${RAVENCOIN_SOURCE_REPOSITORY}/archive/${RAVENCOIN_SOURCE_COMMIT}.tar.gz"; \
    printf '%s  %s\n' "$RAVENCOIN_SOURCE_ARCHIVE_SHA256" "$archive" \
       | sha256sum --check --strict; \
    mkdir source; \
    tar --extract --gzip --file "$archive" --strip-components=1 --directory source; \
    /tmp/probe/apply-to-source.sh /tmp/candidate/source

WORKDIR /tmp/candidate/source
RUN set -eux; \
    ./autogen.sh; \
    ./configure --disable-wallet --without-gui --without-miniupnpc \
       --disable-bench; \
    make -j"$(nproc)" -C src ravend raven-cli test/test_raven; \
    mkdir -p /out/bin; \
    install -m0555 src/ravend src/raven-cli src/test/test_raven -t /out/bin; \
    mkdir -p /out/source; \
    cp -a /tmp/candidate/source/. /out/source/
