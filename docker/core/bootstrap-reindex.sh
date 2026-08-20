#!/bin/sh
set -eu

datadir=/var/lib/ravencoin
blocks_marker="$datadir/.chainstrap-blocks-ready.json"
done_marker="$datadir/.chainstrap-reindex-complete"

fail() {
    printf 'ravencoin-bootstrap-reindex: %s\n' "$1" >&2
    exit 1
}

[ -s "$blocks_marker" ] || fail 'missing vetted ChainStrap block marker'
set -- $(sha256sum "$blocks_marker")
marker_hash=$1

# The marker is written by our own stdlib JSON writer with sorted keys and
# indentation. Parse only the two consensus facts we need, and fail closed if
# the file is not in that expected form. The final Core image deliberately has
# no jq/Python dependency.
snapshot_height=$(sed -n 's/^[[:space:]]*"height":[[:space:]]*\([0-9][0-9]*\),[[:space:]]*$/\1/p' "$blocks_marker")
snapshot_hash=$(sed -n 's/^[[:space:]]*"blockhash":[[:space:]]*"\([0-9a-f][0-9a-f]*\)",[[:space:]]*$/\1/p' "$blocks_marker")
case "$snapshot_height" in
    ''|*[!0-9]*) fail 'bootstrap marker has no valid snapshot height' ;;
esac
case "$snapshot_hash" in
    ''|*[!0-9a-f]*) fail 'bootstrap marker has no valid snapshot block hash' ;;
esac
[ "${#snapshot_hash}" -eq 64 ] || fail 'bootstrap marker block hash is not 64 hex characters'

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
# reindex Core may encounter stale/orphan/invalid records in blk*.dat without
# turning that into a process-level error. Re-open the resulting databases with
# networking still disabled and require the active chain to terminate at the
# exact height/hash asserted by the vetted ChainStrap manifest before creating
# the completion marker.
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

[ "$observed_height" = "$snapshot_height" ] \
    || fail "validated Core tip height $observed_height does not equal snapshot height $snapshot_height"
[ "$observed_tip" = "$snapshot_hash" ] \
    || fail "validated Core tip hash $observed_tip does not equal snapshot hash $snapshot_hash"
[ "$observed_snapshot_hash" = "$snapshot_hash" ] \
    || fail "Core hash at snapshot height does not equal the vetted snapshot hash"

rpc stop >/dev/null
wait "$probe_pid"
probe_pid=
trap - EXIT HUP INT TERM

temporary_marker="${done_marker}.new.$$"
printf '%s\n' "$marker_hash" > "$temporary_marker"
chmod 600 "$temporary_marker"
mv "$temporary_marker" "$done_marker"
printf '%s\n' \
    "Full local Core reindex completed and exact snapshot tip $snapshot_height:$snapshot_hash was verified; normal Core startup is now allowed."
