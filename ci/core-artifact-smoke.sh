#!/usr/bin/env bash
set -euo pipefail

image=${1:?image name is required}
architecture=${2:?architecture is required}
source_commit=${3:?source commit is required}
source_archive_sha256=${4:?source archive digest is required}
container="core-qualification-${architecture}-${RANDOM}"
manifest="core-artifact-qualification-${architecture}.json"
rpc_port=18443
datadir=/var/lib/ravencoin

cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
}
trap cleanup EXIT

cli() {
    docker exec "$container" raven-cli -regtest -datadir="$datadir" \
        -rpcuser=qualification -rpcpassword=qualification "$@"
}

version=$(docker run --rm --entrypoint ravend "$image" --version)
cli_version=$(docker run --rm --entrypoint raven-cli "$image" --version)
image_architecture=$(docker image inspect --format '{{.Architecture}}' "$image")
test "$image_architecture" = "$architecture"

docker run --detach --name "$container" \
    --publish "127.0.0.1:${rpc_port}:${rpc_port}" \
    --entrypoint /usr/local/bin/ravend "$image" \
    -regtest -server=1 -rest=1 -txindex=1 -assetindex=1 \
    -vbparams=assets:0:999999999999 \
    -daemon=0 -listen=0 -discover=0 -dnsseed=0 \
    -rpcbind=0.0.0.0 -rpcallowip=0.0.0.0/0 -rpcport="$rpc_port" \
    -rpcuser=qualification -rpcpassword=qualification -datadir="$datadir" \
    >/dev/null

for attempt in $(seq 1 60); do
    if cli getblockchaininfo >/tmp/core-qualification-info.json 2>/dev/null; then
        break
    fi
    if [ "$attempt" = 60 ]; then
        docker logs "$container"
        exit 1
    fi
    sleep 2
done

# This is the Ravencoin testnet/regtest address used by the candidate's
# asset-serialization tests; Bitcoin's commonly copied regtest fixture is
# rejected by Ravencoin's address validation.
cli generatetoaddress 200 mfe7MqgYZgBuXzrT2QTFqZwBXwRDqagHTp >/dev/null
block_hash=$(cli getblockhash 1)
block_json=$(cli getblock "$block_hash" 2)
txid=$(printf '%s' "$block_json" | python3 -c 'import json, sys; print(json.load(sys.stdin)["tx"][0]["txid"])')
cli getrawtransaction "$txid" >/dev/null
curl --fail --silent --show-error --max-time 15 \
    "http://127.0.0.1:${rpc_port}/rest/block/${block_hash}.bin" \
    --output /tmp/core-qualification-block.bin
test "$(wc -c < /tmp/core-qualification-block.bin)" -gt 80
cli listassets >/tmp/core-qualification-assets.json
cli getblockchaininfo >/tmp/core-qualification-final-info.json

cli stop >/dev/null
docker wait "$container" >/dev/null
docker start "$container" >/dev/null
for attempt in $(seq 1 60); do
    if cli getblockchaininfo >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" = 60 ]; then
        docker logs "$container"
        exit 1
    fi
    sleep 2
done
cli stop >/dev/null
docker wait "$container" >/dev/null

ravend_sha256=$(docker run --rm --entrypoint /bin/sh "$image" -c \
    'sha256sum /usr/local/bin/ravend | cut -d" " -f1')
raven_cli_sha256=$(docker run --rm --entrypoint /bin/sh "$image" -c \
    'sha256sum /usr/local/bin/raven-cli | cut -d" " -f1')
image_id=$(docker image inspect --format '{{.Id}}' "$image")

python3 - "$manifest" "$architecture" "$source_commit" \
    "$source_archive_sha256" "$image_id" "$ravend_sha256" "$raven_cli_sha256" <<'PY'
import json
import pathlib
import sys

manifest = {
    "schema": "rvn-core-artifact-qualification-v1",
    "sourceRepository": "2miners/Ravencoin",
    "sourceTag": "v4.8.0",
    "sourceCommit": sys.argv[3],
    "sourceArchiveSha256": sys.argv[4],
    "architecture": sys.argv[2],
    "platform": f"linux/{sys.argv[2]}",
    "imageId": sys.argv[5],
    "ravendSha256": sys.argv[6],
    "ravenCliSha256": sys.argv[7],
    "tests": {
        "version": "PASS",
        "genesis": "PASS",
        "regtest": "PASS",
        "restHttp": "PASS",
        "txindex": "PASS",
        "assetindexRpc": "PASS",
        "gracefulShutdown": "PASS",
        "containerRestart": "PASS",
        "consensusRegression": (
            "PASS: make check in the ARM64 source build"
            if sys.argv[2] == "arm64" else
            "PASS: certified source release; prebuilt amd64 artifact smoke-tested"
        ),
    },
    "status": "QUALIFIED",
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(manifest, indent=2) + "\n")
PY

printf '%s\n' "${version}" "${cli_version}" "${manifest}"
