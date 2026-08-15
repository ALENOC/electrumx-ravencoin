FROM python:3.12-slim-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential cmake git libleveldb-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .
RUN python -m venv /opt/electrumx \
    && /opt/electrumx/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/electrumx/bin/pip install --no-cache-dir .

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libleveldb1d libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --home-dir /var/lib/electrumx electrumx

COPY --from=builder /opt/electrumx /opt/electrumx
COPY --chmod=0555 contrib/docker/electrumx-entrypoint.sh \
    /usr/local/bin/electrumx-entrypoint

ENV PATH="/opt/electrumx/bin:$PATH" \
    COIN=Ravencoin \
    DB_DIRECTORY=/var/lib/electrumx/db \
    SERVICES="tcp://:50001,rpc://127.0.0.1:8000" \
    EVENT_LOOP_POLICY=uvloop

RUN install -d -o electrumx -g electrumx /var/lib/electrumx/db

USER electrumx
WORKDIR /var/lib/electrumx
VOLUME ["/var/lib/electrumx/db"]
EXPOSE 50001 50002
STOPSIGNAL SIGTERM
ENTRYPOINT ["electrumx-entrypoint"]

# DAEMON_URL is deliberately not baked into the image.  Supply a private or
# localhost URL at runtime.  Operators enable TLS only after mounting their own
# certificate and key and updating SERVICES/SSL_CERTFILE/SSL_KEYFILE.
CMD ["electrumx_server"]
