#!/bin/sh
set -eu

data_dir=${RAVENCOIN_DATA_DIR:-/var/lib/ravencoin}
config_dir=${RAVENCOIN_CONFIG_DIR:-/var/lib/ravencoin-config}
config_file="$config_dir/raven.conf"
rpc_user_file=${RAVEN_RPC_USER_FILE:-/run/raven-secrets/raven_rpc_user}
rpc_password_file=${RAVEN_RPC_PASSWORD_FILE:-/run/raven-secrets/raven_rpc_password}

read_secret() {
    secret_file=$1
    secret_name=$2
    if [ ! -s "$secret_file" ]; then
        printf 'missing or empty %s secret file\n' "$secret_name" >&2
        exit 1
    fi
    secret_value=$(sed -n '1p' "$secret_file")
    case "$secret_value" in
        ''|*[!A-Za-z0-9_-]*)
            printf '%s contains unsupported characters\n' "$secret_name" >&2
            exit 1
            ;;
    esac
    printf '%s' "$secret_value"
}

rpc_user=$(read_secret "$rpc_user_file" RAVEN_RPC_USER)
rpc_password=$(read_secret "$rpc_password_file" RAVEN_RPC_PASSWORD)

if [ ! -e "$config_file" ]; then
    umask 077
    temporary_config="${config_file}.new.$$"
    {
        printf '%s\n' \
            'server=1' \
            'daemon=0' \
            'listen=1' \
            'port=8767' \
            'rpcport=8766' \
            'rpcbind=127.0.0.1' \
            'rpcbind=172.29.80.10' \
            'rpcallowip=172.29.80.0/24' \
            'txindex=1' \
            'assetindex=1' \
            'rest=1' \
            'disablewallet=1'
        printf 'rpcuser=%s\n' "$rpc_user"
        printf 'rpcpassword=%s\n' "$rpc_password"
    } > "$temporary_config"
    chmod 600 "$temporary_config"
    mv "$temporary_config" "$config_file"
else
    configured_user=
    configured_password=
    configured_rest=false
    while IFS= read -r config_line; do
        case "$config_line" in
            rpcuser=*) configured_user=${config_line#rpcuser=} ;;
            rpcpassword=*) configured_password=${config_line#rpcpassword=} ;;
            rest=1) configured_rest=true ;;
        esac
    done < "$config_file"
    # ElectrumX reads raw blocks from Core's REST interface, so a configuration
    # written before that requirement was documented cannot index anything.
    # Set RAVEN_CONFIG_NO_AUTO_REST=1 to opt out of this automatic edit and
    # require rest=1 to already be present instead.
    if [ "$configured_rest" = false ]; then
        if [ "${RAVEN_CONFIG_NO_AUTO_REST:-0}" = "1" ]; then
            printf '%s\n' \
                'rest=1 is required but missing from the existing Core configuration,' \
                'and RAVEN_CONFIG_NO_AUTO_REST=1 disables automatic config edits.' \
                'Add rest=1 to raven.conf yourself and restart.' >&2
            exit 1
        fi
        printf 'rest=1\n' >> "$config_file"
        printf '%s\n' 'Added the required rest=1 setting to the existing Core configuration.'
    fi
    if [ "$configured_user" != "$rpc_user" ] || [ "$configured_password" != "$rpc_password" ]; then
        printf '%s\n' \
            'Ravencoin RPC secrets do not match the persistent configuration.' \
            'Refusing to overwrite either value automatically.' >&2
        exit 1
    fi
fi

# ChainStrap pending-validation gate (GLM53-RVN-004): staged raw block data
# must never be consumed by normal startup with the default assumevalid
# checkpoint. If the blocks marker exists, the one-shot bootstrap-reindex
# container must have completed full validation first. The Compose dependency
# graph enforces this ordering, but the container itself must fail closed so
# the invariant holds for direct invocations, restarts, and overlay removal.
chainstrap_blocks_marker="$data_dir/.chainstrap-blocks-ready.json"
chainstrap_done_marker="$data_dir/.chainstrap-reindex-complete"
if [ -s "$chainstrap_blocks_marker" ]; then
    if [ -s "$chainstrap_done_marker" ]; then
        # Never use "set --" here: it would replace the container arguments
        # that are forwarded to ravend below, so a validated ChainStrap
        # installation would crash loop on its first normal startup.
        blocks_hash=$(sha256sum "$chainstrap_blocks_marker" | cut -d' ' -f1)
        done_hash=$(sed -n '1p' "$chainstrap_done_marker")
        if [ "$blocks_hash" != "$done_hash" ]; then
            printf '%s\n' \
                'ChainStrap reindex-complete marker does not match the block' \
                'bootstrap marker. Refusing normal startup: the staged block data' \
                'has not been fully validated for this snapshot.' >&2
            exit 1
        fi
    else
        printf '%s\n' \
            'ChainStrap raw block data is staged but has not completed full' \
            'validation (-reindex -assumevalid=0). Refusing normal startup;' \
            'run the ravencoin-bootstrap-reindex service first.' >&2
        exit 1
    fi
fi

exec ravend \
    -datadir="$data_dir" \
    -conf="$config_file" \
    -printtoconsole \
    "$@"
