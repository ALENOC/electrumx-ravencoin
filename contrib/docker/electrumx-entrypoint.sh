#!/bin/sh
set -eu

if [ -z "${DAEMON_URL:-}" ]; then
    rpc_user_file=${RAVEN_RPC_USER_FILE:-/run/raven-secrets/raven_rpc_user}
    rpc_password_file=${RAVEN_RPC_PASSWORD_FILE:-/run/raven-secrets/raven_rpc_password}

    if [ ! -s "$rpc_user_file" ] || [ ! -s "$rpc_password_file" ]; then
        printf '%s\n' 'DAEMON_URL is unset and bundled-Core RPC secrets are unavailable.' >&2
        exit 1
    fi

    rpc_user=$(sed -n '1p' "$rpc_user_file")
    rpc_password=$(sed -n '1p' "$rpc_password_file")
    case "$rpc_user$rpc_password" in
        ''|*[!A-Za-z0-9_-]*)
            printf '%s\n' 'Ravencoin RPC secrets contain unsupported characters.' >&2
            exit 1
            ;;
    esac

    daemon_host=${RAVENCOIN_DAEMON_HOST:-ravencoin-core}
    daemon_port=${RAVENCOIN_DAEMON_PORT:-8766}
    export DAEMON_URL="http://${rpc_user}:${rpc_password}@${daemon_host}:${daemon_port}/"
fi

exec "$@"
