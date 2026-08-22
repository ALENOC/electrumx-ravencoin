#!/bin/sh
set -eu

datadir=/var/lib/ravencoin
blocks_marker="$datadir/.chainstrap-blocks-ready.json"
done_marker="$datadir/.chainstrap-reindex-complete"
release_floor_height=4501329
release_floor_hash=000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48

fail() {
    printf 'ravencoin-bootstrap-reindex: %s\n' "$1" >&2
    exit 1
}

[ -s "$blocks_marker" ] || fail 'missing vetted ChainStrap block marker'
set -- $(sha256sum "$blocks_marker")
marker_hash=$1

# The marker is written by our own stdlib JSON writer with sorted keys and
# indentation. Parse only consensus/load-bearing facts and fail closed if the
# file is not in that expected form. The final Core image deliberately has no
# jq/Python dependency.
snapshot_height=$(sed -n 's/^[[:space:]]*"height":[[:space:]]*\([0-9][0-9]*\),[[:space:]]*$/\1/p' "$blocks_marker")
snapshot_hash=$(sed -n 's/^[[:space:]]*"blockhash":[[:space:]]*"\([0-9a-f][0-9a-f]*\)",[[:space:]]*$/\1/p' "$blocks_marker")
marker_floor_height=$(sed -n 's/^[[:space:]]*"release_floor_height":[[:space:]]*\([0-9][0-9]*\),[[:space:]]*$/\1/p' "$blocks_marker")
marker_floor_hash=$(sed -n 's/^[[:space:]]*"release_floor_blockhash":[[:space:]]*"\([0-9a-f][0-9a-f]*\)",[[:space:]]*$/\1/p' "$blocks_marker")
case "$snapshot_height" in
    ''|*[!0-9]*) fail 'bootstrap marker has no valid snapshot height' ;;
esac
case "$snapshot_hash" in
    ''|*[!0-9a-f]*) fail 'bootstrap marker has no valid snapshot block hash' ;;
esac
[ "${#snapshot_hash}" -eq 64 ] || fail 'bootstrap marker block hash is not 64 hex characters'
case "$marker_floor_height" in
    ''|*[!0-9]*) fail 'bootstrap marker has no valid release-floor height' ;;
esac
case "$marker_floor_hash" in
    ''|*[!0-9a-f]*) fail 'bootstrap marker has no valid release-floor block hash' ;;
esac
[ "${#marker_floor_hash}" -eq 64 ] || fail 'bootstrap marker release-floor hash is not 64 hex characters'
[ "$marker_floor_height" = "$release_floor_height" ] \
    || fail 'bootstrap marker attempted to change the release-floor height'
[ "$marker_floor_hash" = "$release_floor_hash" ] \
    || fail 'bootstrap marker attempted to change the release-floor block hash'
[ "$snapshot_height" -ge "$release_floor_height" ] \
    || fail 'bootstrap snapshot height is below the release floor'

if [ -s "$done_marker" ]; then
    done_hash=$(sed -n '1p' "$done_marker")
    [ "$done_hash" = "$marker_hash" ] \
        || fail 'reindex marker belongs to a different block bootstrap manifest'
    printf '%s\n' 'Full Core reindex for this ChainStrap snapshot is already complete.'
    exit 0
fi

printf '%s\n' \
    'Starting full local Ravencoin Core validation of ChainStrap raw block files.' \
    'No downloaded chainstate, block index, asset index, or undo data is trusted.'

ravend \
    -datadir="$datadir" \
    -conf=/dev/null \
    -printtoconsole \
    -server=0 \
    -listen=0 \
    -connect=0 \
    -dnsseed=0 \
    -discover=0 \
    -disablewallet=1 \
    -txindex=1 \
    -assetindex=1 \
    -reindex=1 \
    -assumevalid=0 \
    -stopafterblockimport=1

# A successful block-file import exit is necessary but not sufficient. During
# reindex Core may encounter stale/orphan records and Ravencoin deliberately
# tolerates one historical transfer-deserialization condition while loading raw
# blk*.dat records (fFromLoad). Re-open the resulting databases with networking
# still disabled. Require both the exact resolved snapshot tip and ancestry at
# the release-embedded floor before blessing the bootstrap. Offline validation
# proves consensus validity/ancestry, not that an alternate valid fork is the
# current network heaviest chain; normal P2P establishes that after startup.
rpc_user=chainstrap-verify
rpc_password=chainstrap-local-offline-only
probe_pid=

rpc() {
    raven-cli \
        -datadir="$datadir" \
        -conf=/dev/null \
        -rpcuser="$rpc_user" \
        -rpcpassword="$rpc_password" \
        "$@"
}

cleanup_probe() {
    if [ -n "${probe_pid:-}" ] && kill -0 "$probe_pid" 2>/dev/null; then
        rpc stop >/dev/null 2>&1 || kill "$probe_pid" 2>/dev/null || true
        wait "$probe_pid" 2>/dev/null || true
    fi
    probe_pid=
}
trap cleanup_probe EXIT HUP INT TERM

ravend \
    -datadir="$datadir" \
    -conf=/dev/null \
    -printtoconsole=0 \
    -server=1 \
    -listen=0 \
    -connect=0 \
    -dnsseed=0 \
    -discover=0 \
    -disablewallet=1 \
    -txindex=1 \
    -assetindex=1 \
    -assumevalid=0 \
    -rpcbind=127.0.0.1 \
    -rpcallowip=127.0.0.1 \
    -rpcuser="$rpc_user" \
    -rpcpassword="$rpc_password" &
probe_pid=$!

attempt=0
while ! rpc getblockchaininfo >/dev/null 2>&1; do
    if ! kill -0 "$probe_pid" 2>/dev/null; then
        wait "$probe_pid" 2>/dev/null || true
        probe_pid=
        fail 'Core exited before the post-reindex verification RPC became ready'
    fi
    attempt=$((attempt + 1))
    [ "$attempt" -lt 120 ] || fail 'timed out waiting for post-reindex Core verification RPC'
    sleep 1
done

observed_height=$(rpc getblockcount | tr -d '\r\n[:space:]')
observed_tip=$(rpc getbestblockhash | tr -d '\r\n[:space:]')
observed_snapshot_hash=$(rpc getblockhash "$snapshot_height" | tr -d '\r\n[:space:]')
observed_floor_hash=$(rpc getblockhash "$release_floor_height" | tr -d '\r\n[:space:]')

[ "$observed_height" = "$snapshot_height" ] \
    || fail "validated Core tip height $observed_height does not equal snapshot height $snapshot_height"
[ "$observed_tip" = "$snapshot_hash" ] \
    || fail "validated Core tip hash $observed_tip does not equal snapshot hash $snapshot_hash"
[ "$observed_snapshot_hash" = "$snapshot_hash" ] \
    || fail 'Core hash at snapshot height does not equal the resolved snapshot hash'
[ "$observed_floor_hash" = "$release_floor_hash" ] \
    || fail "release-floor ancestry mismatch at height $release_floor_height"
printf '%s\n' \
    "Release-floor ancestry verified at $release_floor_height:$release_floor_hash."

# The snapshot is far beyond Ravencoin asset activation and this deployment
# requires -assetindex for ElectrumX/RVN operation. Exercise both the asset
# metadata DB and the address-by-asset index before blessing the bootstrap.
# This is intentionally a read-only RPC probe; it does not repair or mutate an
# index. If either database is unavailable/inconsistent, no completion marker
# is written and normal networked startup remains blocked.
asset_listing=$(rpc listassets "*" false 1 0) \
    || fail 'post-reindex asset database probe failed'
sample_asset=$(printf '%s\n' "$asset_listing" \
    | sed -n 's/^[[:space:]]*"\([^\"]\{1,40\}\)"[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p' \
    | sed -n '1p')
[ -n "$sample_asset" ] \
    || fail 'post-reindex asset database returned no sample asset at the ChainStrap snapshot tip'
rpc getassetdata "$sample_asset" >/dev/null \
    || fail "post-reindex asset metadata lookup failed for $sample_asset"
asset_index_probe=$(rpc listaddressesbyasset "$sample_asset" true) \
    || fail "post-reindex asset index lookup failed for $sample_asset"
case "$asset_index_probe" in
    *"not functional unless -assetindex is enabled"*)
        fail 'post-reindex Core reports that assetindex is not enabled/usable'
        ;;
esac

rpc stop >/dev/null
wait "$probe_pid"
probe_pid=
trap - EXIT HUP INT TERM

temporary_marker="${done_marker}.new.$$"
printf '%s\n' "$marker_hash" > "$temporary_marker"
chmod 600 "$temporary_marker"
mv "$temporary_marker" "$done_marker"
printf '%s\n' \
    "Full local Core reindex completed; exact snapshot tip $snapshot_height:$snapshot_hash, release-floor ancestry, and asset database/index probes were verified. Normal Core startup is now allowed."
