FROM debian:bullseye-slim@sha256:f313b4bd62667092a59b3a664d7d3ab8b5e65f41675f48e81455a15dc5abe792

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 ravencoin \
    && useradd --uid 10001 --gid 10001 --system \
       --home-dir /var/lib/ravencoin --shell /usr/sbin/nologin ravencoin \
    && install -d -o ravencoin -g ravencoin \
       /var/lib/ravencoin /opt/electrumx-ravencoin/bootstrap

# The transport/downloader remains importable as a library. Production starts
# through a floor-binding entrypoint which refuses to invoke the runtime resolver
# unless the committed reviewed floor manifest exactly matches compiled policy.
COPY --chmod=0444 contrib/bootstrap/chainstrap_bootstrap.py \
    /opt/electrumx-ravencoin/bootstrap/chainstrap_bootstrap.py
COPY --chmod=0444 contrib/bootstrap/chainstrap_runtime_base.py \
    /opt/electrumx-ravencoin/bootstrap/chainstrap_runtime_base.py
COPY --chmod=0444 contrib/bootstrap/chainstrap_runtime.py \
    /opt/electrumx-ravencoin/bootstrap/chainstrap_runtime.py
COPY --chmod=0444 contrib/bootstrap/manifests/rvn-mainnet-2026-08-19.json \
    /opt/electrumx-ravencoin/bootstrap/release-floor.json
COPY --chmod=0555 contrib/bootstrap/chainstrap_entrypoint.py \
    /usr/local/bin/chainstrap-bootstrap

ENV PYTHONPATH=/opt/electrumx-ravencoin/bootstrap \
    CHAINSTRAP_RELEASE_FLOOR=/opt/electrumx-ravencoin/bootstrap/release-floor.json
USER ravencoin
WORKDIR /var/lib/ravencoin
VOLUME ["/var/lib/ravencoin"]
ENTRYPOINT ["chainstrap-bootstrap"]
CMD ["--datadir", "/var/lib/ravencoin"]
