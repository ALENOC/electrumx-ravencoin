#!/bin/sh
set -eu

mode=bundled
enable_reboot=false

usage() {
    cat <<'EOF'
Usage: ./setup.sh [--bundled-core | --existing-core] [--enable-reboot]

  --bundled-core  Prepare the recommended Core 4.8.0 + ElectrumX stack (default).
  --existing-core Prepare ElectrumX for an already-running private Core >= 4.8.0.
  --enable-reboot Install and enable a user systemd unit for the bundled stack.
EOF
}

fail() {
    printf 'setup: %s\n' "$1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --bundled-core) mode=bundled ;;
        --existing-core) mode=existing ;;
        --enable-reboot) enable_reboot=true ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

[ "$mode" = bundled ] || [ "$enable_reboot" = false ] \
    || fail '--enable-reboot currently supports bundled-Core mode only'
command -v docker >/dev/null 2>&1 || fail 'Docker is not installed or not on PATH'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is required'
docker info >/dev/null 2>&1 || fail 'the current user cannot reach the Docker daemon'

if [ ! -e .env ]; then
    cp .env.example .env
    chmod 600 .env
fi

if [ "$mode" = existing ]; then
    if [ ! -e contrib/electrumx.env ]; then
        cp contrib/electrumx.env.example contrib/electrumx.env
        chmod 600 contrib/electrumx.env
        printf '%s\n' \
            'Created contrib/electrumx.env.' \
            'Edit its CHANGE_ME values and .env TLS settings before starting.'
    else
        printf '%s\n' 'Preserved existing contrib/electrumx.env.'
    fi
    docker compose -f compose.existing-core.yaml config --quiet
    printf '%s\n' \
        'Existing-Core configuration is structurally valid.' \
        'Next: docker compose -f compose.existing-core.yaml up -d --build'
    exit 0
fi

case "$(uname -m)" in
    x86_64|amd64) ;;
    aarch64|arm64)
        fail 'the verified bundled Core 4.8.0 artifact is amd64-only; use --existing-core on arm64'
        ;;
    *) fail 'unsupported CPU architecture for the bundled Core artifact' ;;
esac

command -v openssl >/dev/null 2>&1 || fail 'openssl is required to generate RPC credentials'

umask 077
if [ -e .secrets/raven_rpc_user ] || [ -e .secrets/raven_rpc_password ]; then
    [ -s .secrets/raven_rpc_user ] || fail 'existing RPC username secret is missing or empty'
    [ -s .secrets/raven_rpc_password ] || fail 'existing RPC password secret is missing or empty'
    [ "$(wc -l < .secrets/raven_rpc_user)" -eq 1 ] \
        && LC_ALL=C grep -Eq '^[A-Za-z0-9_-]+$' .secrets/raven_rpc_user \
        || fail 'existing RPC username contains unsupported characters'
    [ "$(wc -l < .secrets/raven_rpc_password)" -eq 1 ] \
        && LC_ALL=C grep -Eq '^[A-Za-z0-9_-]+$' .secrets/raven_rpc_password \
        || fail 'existing RPC password contains unsupported characters'
    chmod 600 .secrets/raven_rpc_user .secrets/raven_rpc_password
    printf '%s\n' 'Preserved existing RPC credential files.'
else
    mkdir -p .secrets
    rpc_user="electrumx_$(openssl rand -hex 8)"
    rpc_password=$(openssl rand -hex 32)
    printf '%s\n' "$rpc_user" > .secrets/raven_rpc_user
    printf '%s\n' "$rpc_password" > .secrets/raven_rpc_password
    chmod 600 .secrets/raven_rpc_user .secrets/raven_rpc_password
    unset rpc_user rpc_password
    printf '%s\n' 'Generated private RPC credential files without displaying their values.'
fi

docker compose config --quiet

if [ "$enable_reboot" = true ]; then
    command -v systemctl >/dev/null 2>&1 \
        || fail 'systemctl is required for --enable-reboot'
    docker_path=$(command -v docker)
    unit_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
    unit_file=$unit_dir/electrumx-ravencoin.service
    mkdir -p "$unit_dir"
    unit_content=$(
        printf '%s\n' \
            '[Unit]' \
            'Description=Ravencoin Core 4.8.0 and ElectrumX' \
            'Wants=network-online.target' \
            'After=network-online.target' \
            'StartLimitIntervalSec=300' \
            'StartLimitBurst=5' \
            '' \
            '[Service]' \
            'Type=oneshot' \
            'RemainAfterExit=yes'
        printf 'WorkingDirectory=%s\n' "$PWD"
        printf 'ExecStart=%s compose up -d\n' "$docker_path"
        printf 'ExecStop=%s compose stop\n' "$docker_path"
        printf '%s\n' \
            'TimeoutStartSec=0' \
            'TimeoutStopSec=1800' \
            'Restart=on-failure' \
            'RestartSec=15s' \
            '' \
            '[Install]' \
            'WantedBy=default.target'
    )
    if [ -e "$unit_file" ]; then
        existing_unit=$(sed -n '1,$p' "$unit_file")
        [ "$existing_unit" = "$unit_content" ] \
            || fail "refusing to overwrite different existing $unit_file"
        printf '%s\n' 'Preserved the existing matching user service.'
    else
        umask 022
        printf '%s\n' "$unit_content" > "$unit_file"
        chmod 644 "$unit_file"
    fi
    wants_dir=$unit_dir/default.target.wants
    wants_link=$wants_dir/electrumx-ravencoin.service
    mkdir -p "$wants_dir"
    if [ -L "$wants_link" ]; then
        [ "$(readlink "$wants_link")" = ../electrumx-ravencoin.service ] \
            || fail "refusing to replace different existing $wants_link"
    elif [ -e "$wants_link" ]; then
        fail "refusing to replace different existing $wants_link"
    else
        ln -s ../electrumx-ravencoin.service "$wants_link"
    fi
    if systemctl --user is-system-running >/dev/null 2>&1; then
        systemctl --user daemon-reload
    fi
    if command -v loginctl >/dev/null 2>&1 \
            && ! loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null \
                 | grep -qx yes; then
        printf '%s\n' \
            'Warning: user lingering is disabled.' \
            'An administrator must run: loginctl enable-linger <operator-user>' >&2
    fi
    printf '%s\n' 'Enabled the user service for reboot recovery; it was not started.'
fi

printf '%s\n' \
    'Bundled Core 4.8.0 + ElectrumX configuration is valid.' \
    'Next: docker compose up -d --build' \
    'Then: docker compose ps'
