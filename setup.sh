#!/bin/sh
set -eu

mode=bundled
enable_reboot=false
configure_ddns=false

usage() {
    cat <<'EOF'
Usage: ./setup.sh [--bundled-core | --existing-core] [--enable-reboot]
                  [--configure-ddns]

  --bundled-core   Prepare the recommended Core 4.8.0 + ElectrumX stack (default).
  --existing-core  Prepare ElectrumX for an already-running private Core deployment;
                   exact backend identity and signed-policy/live checks still apply.
  --enable-reboot  Install and enable a user systemd unit for the bundled stack.
  --configure-ddns Optionally point a free DuckDNS hostname at this host, for a
                   public node on a dynamic residential IP address.  Never
                   required for a private or LAN-only deployment.
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
        --configure-ddns) configure_ddns=true ;;
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

set_env_value() {
    setting_key=$1
    setting_value=$2
    if grep -q "^${setting_key}=" .env; then
        sed_script="s|^${setting_key}=.*|${setting_key}=${setting_value}|"
        # Unpredictable private temporary (GLM53-RVN-014): a predictable
        # .env.new.$$ name lets a local attacker pre-plant a symlink that the
        # redirect follows into an arbitrary file.
        temporary_env=$(mktemp .env.new.XXXXXXXXXX)
        chmod 600 "$temporary_env"
        sed "$sed_script" .env > "$temporary_env"
        mv "$temporary_env" .env
    else
        printf '%s=%s\n' "$setting_key" "$setting_value" >> .env
    fi
}

read_secret_quietly() {
    secret_prompt=$1
    printf '%s' "$secret_prompt" >&2
    if [ -t 0 ]; then
        stty_state=$(stty -g)
        stty -echo
        IFS= read -r secret_value
        stty "$stty_state"
        printf '\n' >&2
    else
        IFS= read -r secret_value
    fi
}

install_user_unit() {
    unit_name=$1
    unit_body=$2
    unit_target=$3
    unit_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
    mkdir -p "$unit_dir"
    unit_path="$unit_dir/$unit_name"
    if [ -e "$unit_path" ]; then
        existing_unit=$(sed -n '1,$p' "$unit_path")
        [ "$existing_unit" = "$unit_body" ] \
            || fail "refusing to overwrite different existing $unit_path"
        printf '%s\n' "Preserved the existing matching $unit_name."
    else
        (umask 022; printf '%s\n' "$unit_body" > "$unit_path")
        chmod 644 "$unit_path"
    fi
    if [ -z "$unit_target" ]; then
        return 0
    fi
    wants_dir="$unit_dir/${unit_target}.wants"
    wants_link="$wants_dir/$unit_name"
    mkdir -p "$wants_dir"
    if [ -L "$wants_link" ]; then
        [ "$(readlink "$wants_link")" = "../$unit_name" ] \
            || fail "refusing to replace different existing $wants_link"
    elif [ -e "$wants_link" ]; then
        fail "refusing to replace different existing $wants_link"
    else
        ln -s "../$unit_name" "$wants_link"
    fi
}

configure_duckdns() {
    command -v python3 >/dev/null 2>&1 \
        || fail 'python3 is required on the host for the DuckDNS updater'

    ddns_domain=
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        printf 'DuckDNS hostname, subname only, for example my-ravencoin-node: ' >&2
        IFS= read -r ddns_domain_input || ddns_domain_input=
        case "$ddns_domain_input" in
            *.duckdns.org)
                printf '%s\n' 'Enter only the subname, without .duckdns.org.' >&2
                continue
                ;;
        esac
        if printf '%s' "$ddns_domain_input" \
                | LC_ALL=C grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'; then
            ddns_domain=$ddns_domain_input
            break
        fi
        printf '%s\n' \
            'Use 1 to 63 lowercase letters, digits or hyphens, not starting or ending with a hyphen.' >&2
    done
    [ -n "$ddns_domain" ] || fail 'no valid DuckDNS hostname was supplied'

    read_secret_quietly 'DuckDNS token (not echoed): '
    printf '%s' "$secret_value" \
        | LC_ALL=C grep -Eq '^[A-Za-z0-9_-]{8,200}$' \
        || fail 'that does not look like a DuckDNS token; nothing was written'

    mkdir -p .secrets
    (umask 077; printf '%s\n' "$secret_value" > .secrets/duckdns_token)
    chmod 600 .secrets/duckdns_token
    secret_value=
    unset secret_value

    printf 'Also publish an IPv6 address if this host has a global one? [y/N]: ' >&2
    IFS= read -r ddns_ipv6_answer || ddns_ipv6_answer=
    case "$ddns_ipv6_answer" in
        y|Y|yes|YES) ddns_ipv6_mode=auto ;;
        *) ddns_ipv6_mode=off ;;
    esac

    set_env_value DDNS_PROVIDER duckdns
    set_env_value DUCKDNS_DOMAIN "$ddns_domain"
    set_env_value DUCKDNS_IPV6 "$ddns_ipv6_mode"
    current_public_host=$(sed -n 's|^ELECTRUMX_PUBLIC_HOST=||p' .env | sed -n '1p')
    case "$current_public_host" in
        ''|electrum.example.org)
            set_env_value ELECTRUMX_PUBLIC_HOST "${ddns_domain}.duckdns.org"
            ;;
        *)
            printf '%s\n' \
                "Left ELECTRUMX_PUBLIC_HOST as ${current_public_host}; edit .env if that is wrong."
            ;;
    esac

    command -v systemctl >/dev/null 2>&1 || {
        printf '%s\n' \
            'systemctl is unavailable, so no timer was installed.' \
            "Run this every 5 minutes instead, from $PWD:" \
            "  python3 contrib/ddns/duckdns_update.py --domain ${ddns_domain} --ipv6 ${ddns_ipv6_mode}"
        return 0
    }

    python_path=$(command -v python3)
    service_body=$(
        printf '%s\n' \
            '[Unit]' \
            'Description=DuckDNS hostname update for ElectrumX Ravencoin' \
            'Wants=network-online.target' \
            'After=network-online.target' \
            '' \
            '[Service]' \
            'Type=oneshot' \
            'StateDirectory=electrumx-ravencoin'
        printf 'WorkingDirectory=%s\n' "$PWD"
        printf 'ExecStart=%s %s/contrib/ddns/duckdns_update.py --domain %s --ipv6 %s --token-file %s/.secrets/duckdns_token --state-file %%S/electrumx-ravencoin/duckdns.json\n' \
            "$python_path" "$PWD" "$ddns_domain" "$ddns_ipv6_mode" "$PWD"
        printf '%s\n' \
            'PrivateTmp=true' \
            'NoNewPrivileges=true'
    )
    timer_body=$(
        printf '%s\n' \
            '[Unit]' \
            'Description=Periodic DuckDNS hostname update for ElectrumX Ravencoin' \
            '' \
            '[Timer]' \
            'OnBootSec=2min' \
            'OnUnitActiveSec=5min' \
            'AccuracySec=30s' \
            'Persistent=true' \
            'Unit=electrumx-ravencoin-ddns.service' \
            '' \
            '[Install]' \
            'WantedBy=timers.target'
    )
    install_user_unit electrumx-ravencoin-ddns.service "$service_body" ""
    install_user_unit electrumx-ravencoin-ddns.timer "$timer_body" timers.target
    if systemctl --user is-system-running >/dev/null 2>&1; then
        systemctl --user daemon-reload
        systemctl --user start electrumx-ravencoin-ddns.timer 2>/dev/null || true
    fi
    if command -v loginctl >/dev/null 2>&1 \
            && ! loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null \
                 | grep -qx yes; then
        printf '%s\n' \
            'Warning: user lingering is disabled, so the timer stops at logout.' \
            'An administrator must run: loginctl enable-linger <operator-user>' >&2
    fi
    printf '%s\n' \
        "DuckDNS configured for ${ddns_domain}.duckdns.org without displaying the token." \
        'Verify with: systemctl --user list-timers electrumx-ravencoin-ddns.timer' \
        "Then check DNS: dig +short ${ddns_domain}.duckdns.org"
}

if [ ! -e .env ]; then
    cp .env.example .env
    chmod 600 .env
fi

if [ "$mode" = bundled ]; then
    command -v python3 >/dev/null 2>&1         || fail 'python3 is required to select the internal monitor-admin network'
    python3 core-safety/scripts/configure_monitor_admin_network.py --env-file .env         || fail 'could not select a collision-free internal monitor-admin network'
fi

if [ "$configure_ddns" = true ]; then
    printf '%s\n' \
        'Public reachability for this node:' \
        '  [1] I have a static public IP address or my own DNS already' \
        '  [2] My public IP is dynamic, configure a free DuckDNS hostname' \
        '  [3] Skip, this node is private or LAN only'
    printf 'Choice [1/2/3]: '
    IFS= read -r ddns_choice || ddns_choice=3
    case "$ddns_choice" in
        2) configure_duckdns ;;
        1) printf '%s\n' \
               'Nothing to configure here. Set ELECTRUMX_PUBLIC_HOST in .env to your hostname.' ;;
        *) printf '%s\n' 'Skipped public-node DNS configuration.' ;;
    esac
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
    x86_64|amd64|aarch64|arm64) ;;
    *) fail 'unsupported CPU architecture; bundled Core supports x86_64/amd64 and aarch64/arm64' ;;
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

# Validate the release's own Compose model explicitly. A bare
# 'docker compose config' resolves its file set implicitly from COMPOSE_FILE in
# the environment or in .env, so an operator's host-local overlay that is not
# part of a signed release makes this check stat a file the release tree does
# not contain. The updater stages a release with the operator's .env copied in,
# which turned that into an apply-time staging failure. An explicit -f matches
# the default resolution for a clean install and is independent of both
# COMPOSE_FILE channels; --existing-core above already validates this way.
docker compose -f compose.yaml config --quiet

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
