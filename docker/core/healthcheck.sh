#!/bin/sh
set -eu

fail() {
    printf 'ravencoin readiness: %s\n' "$*" >&2
    exit 1
}

cli() {
    raven-cli \
        -datadir=/var/lib/ravencoin \
        -conf=/var/lib/ravencoin-config/raven.conf \
        "$@"
}

max_block_lag=${RAVENCOIN_HEALTH_MAX_BLOCK_LAG:-2}
max_tip_age=${RAVENCOIN_HEALTH_MAX_TIP_AGE:-1800}
min_peers=${RAVENCOIN_HEALTH_MIN_PEERS:-1}

for value in "$max_block_lag" "$max_tip_age" "$min_peers"; do
    case "$value" in
        ''|*[!0-9]*) fail "health threshold is not an unsigned integer: $value" ;;
    esac
done

info=$(cli getblockchaininfo) || fail "getblockchaininfo RPC failed"

blocks=$(printf '%s\n' "$info" | sed -n \
    's/^[[:space:]]*"blocks"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
headers=$(printf '%s\n' "$info" | sed -n \
    's/^[[:space:]]*"headers"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
ibd=$(printf '%s\n' "$info" | sed -n \
    's/^[[:space:]]*"initialblockdownload"[[:space:]]*:[[:space:]]*\([^,[:space:]]*\).*/\1/p' | head -n 1)

case "$blocks" in ''|*[!0-9]*) fail "missing/invalid blocks field" ;; esac
case "$headers" in ''|*[!0-9]*) fail "missing/invalid headers field" ;; esac
[ "$ibd" = "false" ] || fail "initial block download is not complete (initialblockdownload=$ibd)"

[ "$headers" -ge "$blocks" ] || fail "header height $headers is below block height $blocks"
lag=$((headers - blocks))
[ "$lag" -le "$max_block_lag" ] || \
    fail "block lag is $lag (blocks=$blocks headers=$headers max=$max_block_lag)"

peers=$(cli getconnectioncount) || fail "getconnectioncount RPC failed"
case "$peers" in ''|*[!0-9]*) fail "invalid peer count: $peers" ;; esac
[ "$peers" -ge "$min_peers" ] || \
    fail "peer count is $peers (minimum=$min_peers)"

best_hash=$(cli getbestblockhash) || fail "getbestblockhash RPC failed"
[ -n "$best_hash" ] || fail "best block hash is empty"
header=$(cli getblockheader "$best_hash") || fail "getblockheader RPC failed"
tip_time=$(printf '%s\n' "$header" | sed -n \
    's/^[[:space:]]*"time"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
case "$tip_time" in ''|*[!0-9]*) fail "missing/invalid best-block time" ;; esac

now=$(date +%s)
case "$now" in ''|*[!0-9]*) fail "host clock is unavailable" ;; esac
if [ "$tip_time" -gt "$now" ]; then
    tip_age=0
else
    tip_age=$((now - tip_time))
fi
[ "$tip_age" -le "$max_tip_age" ] || \
    fail "best block is ${tip_age}s old (maximum=${max_tip_age}s)"

exit 0
