#!/bin/sh
set -eu

follow=0
case "${1:-}" in
    '') ;;
    --follow|-f) follow=1 ;;
    *)
        printf 'usage: %s [--follow]\n' "$0" >&2
        exit 2
        ;;
esac

state_of() {
    service=$1
    cid=$(docker compose ps -q --all "$service" 2>/dev/null | sed -n '1p')
    if [ -z "$cid" ]; then
        printf 'not-created'
        return
    fi
    status=$(docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null || printf 'unknown')
    if [ "$status" = 'exited' ]; then
        code=$(docker inspect --format '{{.State.ExitCode}}' "$cid" 2>/dev/null || printf '?')
        printf 'exited(%s)' "$code"
    else
        printf '%s' "$status"
    fi
}

bootstrap_state=$(state_of chainstrap-bootstrap)
reindex_state=$(state_of ravencoin-bootstrap-reindex)
core_state=$(state_of ravencoin-core)
electrumx_state=$(state_of electrumx)

case "$bootstrap_state:$reindex_state:$core_state:$electrumx_state" in
    running:*) phase='CHAINSTRAP DOWNLOAD / EXTRACTION' ;;
    exited\(0\):running:*) phase='CORE 4.8.0 OFFLINE FULL REINDEX' ;;
    exited\(0\):exited\(0\):running:*) phase='CORE NORMAL STARTUP / SYNC' ;;
    exited\(0\):exited\(0\):*:running) phase='ELECTRUMX INDEXING / ONLINE' ;;
    *exited\([1-9]*\)*) phase='FAILED - inspect logs below' ;;
    *) phase='STARTING / WAITING FOR DEPENDENCY' ;;
esac

printf '%s\n' \
    'Fast Verified Bootstrap status' \
    "  phase:                       $phase" \
    "  chainstrap-bootstrap:        $bootstrap_state" \
    "  ravencoin-bootstrap-reindex: $reindex_state" \
    "  ravencoin-core:              $core_state" \
    "  electrumx:                   $electrumx_state"

cid=$(docker compose ps -q --all chainstrap-bootstrap 2>/dev/null | sed -n '1p')
if [ -n "$cid" ] && [ "$bootstrap_state" = 'running' ]; then
    printf '\nLive container resources:\n'
    docker stats --no-stream "$cid" || true
fi

printf '\nRecent bootstrap/reindex logs:\n'
docker compose logs --tail=30 chainstrap-bootstrap ravencoin-bootstrap-reindex 2>/dev/null || true

if [ "$follow" -eq 1 ]; then
    printf '\nFollowing all bootstrap phases (Ctrl-C only stops log following):\n'
    exec docker compose logs -f \
        chainstrap-bootstrap ravencoin-bootstrap-reindex ravencoin-core electrumx
fi
