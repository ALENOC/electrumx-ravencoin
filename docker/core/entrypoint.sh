#!/bin/sh
set -eu

data_dir=/var/lib/ravencoin
config_dir=/var/lib/ravencoin-config
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
            'disablewallet=1'
        printf 'rpcuser=%s\n' "$rpc_user"
        printf 'rpcpassword=%s\n' "$rpc_password"
    } > "$temporary_config"
    chmod 600 "$temporary_config"
    mv "$temporary_config" "$config_file"
else
    configured_user=
    configured_password=
    while IFS= read -r config_line; do
        case "$config_line" in
            rpcuser=*) configured_user=${config_line#rpcuser=} ;;
            rpcpassword=*) configured_password=${config_line#rpcpassword=} ;;
        esac
    done < "$config_file"
    if [ "$configured_user" != "$rpc_user" ] || [ "$configured_password" != "$rpc_password" ]; then
        printf '%s\n' \
            'Ravencoin RPC secrets do not match the persistent configuration.' \
            'Refusing to overwrite either value automatically.' >&2
        exit 1
    fi
fi

exec ravend \
    -datadir="$data_dir" \
    -conf="$config_file" \
    -printtoconsole \
    "$@"
