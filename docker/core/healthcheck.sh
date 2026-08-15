#!/bin/sh
set -eu

exec raven-cli \
    -datadir=/var/lib/ravencoin \
    -conf=/var/lib/ravencoin-config/raven.conf \
    getblockchaininfo >/dev/null
