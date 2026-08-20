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

temporary_marker="${done_marker}.new.$$"
printf '%s\n' "$marker_hash" > "$temporary_marker"
chmod 600 "$temporary_marker"
mv "$temporary_marker" "$done_marker"
printf '%s\n' 'Full local Core reindex completed successfully; normal Core startup is now allowed.'
