#!/bin/sh
set -eu

chainstrap_compose='compose.yaml:compose.chainstrap.yaml'

fail() {
    printf 'fast-bootstrap: %s\n' "$1" >&2
    exit 1
}

for arg in "$@"; do
    [ "$arg" != '--existing-core' ] \
        || fail 'ChainStrap fast bootstrap is only available with bundled Core mode'
done

./setup.sh --bundled-core "$@"

[ -f .env ] || fail '.env was not created by setup.sh'
current=$(sed -n 's/^COMPOSE_FILE=//p' .env | sed -n '1p')
case "$current" in
    '')
        printf 'COMPOSE_FILE=%s\n' "$chainstrap_compose" >> .env
        ;;
    "$chainstrap_compose")
        ;;
    *)
        fail "existing COMPOSE_FILE=$current is custom; refusing to overwrite it"
        ;;
esac
chmod 600 .env

docker compose config --quiet
printf '%s\n' \
    'Fast Verified Bootstrap is enabled for this deployment.' \
    'ChainStrap supplies only vetted raw blk*.dat files.' \
    'Ravencoin Core 4.8.0 then performs -reindex with -assumevalid=0.' \
    'ElectrumX cannot start until that validation finishes successfully.' \
    'Next: docker compose up -d --build' \
    "Compose may display 'Waiting' while the one-shot download/reindex gates are active." \
    'Status: ./fast-bootstrap-status.sh' \
    'Follow: ./fast-bootstrap-status.sh --follow'
