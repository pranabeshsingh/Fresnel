#!/bin/bash
set -e

# ==============================================================================
# Fresnel 5G Suite - Automated Deployer
# Deploys Fresnel web GUI, telemetry daemon, and cellular tools over SSH
# ==============================================================================

# Parse options and positional parameters
ENABLE_TELEMETRY=""
TELEMETRY_URL=""
TELEMETRY_TOKEN=""
DRY_RUN_PARSE=0
POSITIONAL_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --no-telemetry|--skip-telemetry)
            ENABLE_TELEMETRY=0
            shift
            ;;
        --telemetry|--enable-telemetry)
            ENABLE_TELEMETRY=1
            shift
            ;;
        --telemetry-url)
            TELEMETRY_URL="$2"
            shift 2
            ;;
        --telemetry-token)
            TELEMETRY_TOKEN="$2"
            shift 2
            ;;
        --dry-run-parse)
            DRY_RUN_PARSE=1
            shift
            ;;
        -h|--help)
            echo "Usage: ./deploy_modem.sh [options] [modem_ip] [ssh_user]"
            echo ""
            echo "Options:"
            echo "  --no-telemetry, --skip-telemetry    Do not enable remote VPS telemetry (default)"
            echo "  --telemetry, --enable-telemetry     Enable remote VPS telemetry pusher"
            echo "  --telemetry-url <URL>               VPS endpoint (e.g. https://modem.yourvps.com:8000)"
            echo "  --telemetry-token <TOKEN>           Bearer token for VPS authentication"
            echo "  -h, --help                          Show this help message"
            exit 0
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

MODEM_IP="${POSITIONAL_ARGS[0]:-172.16.10.1}"
SSH_USER="${POSITIONAL_ARGS[1]:-root}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prompt interactively if telemetry flag was not explicitly provided
if [ -z "$ENABLE_TELEMETRY" ]; then
    if [ -t 0 ]; then
        read -r -p "Enable remote VPS telemetry pusher on modem? [y/N]: " REPLY
        case "$REPLY" in
            [yY][eE][sS]|[yY])
                ENABLE_TELEMETRY=1
                ;;
            *)
                ENABLE_TELEMETRY=0
                ;;
        esac
    else
        ENABLE_TELEMETRY=0
    fi
fi

if [ "$DRY_RUN_PARSE" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

echo "=== Deploying Fresnel 5G Suite to ${SSH_USER}@${MODEM_IP} ==="
if [ "$ENABLE_TELEMETRY" = "1" ]; then
    echo "Telemetry: ENABLED"
else
    echo "Telemetry: DISABLED (Standalone mode)"
fi

SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Test SSH connectivity
echo "[1/5] Checking SSH connection..."
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "uname -a" || {
    echo "ERROR: Unable to connect to ${SSH_USER}@${MODEM_IP} via SSH."
    echo "Usage: ./deploy_modem.sh [options] [modem_ip] [ssh_user]"
    exit 1
}

# Create remote directories on read-write partitions (/usrdata and /data)
echo "[2/5] Creating remote directories on /usrdata and /data..."
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "
    mkdir -p /usrdata/simpleadmin/scripts \
             /usrdata/simpleadmin/www/cgi-bin \
             /usrdata/simpleadmin/bin \
             /data/simpleadmin/data \
             /tmp/gw_sessions
    chmod 700 /tmp/gw_sessions
"

# Copy scripts and web assets
echo "[3/5] Deploying scripts and web assets..."
# Stream scripts
for script in "${SCRIPT_DIR}"/scripts/*; do
    fname="$(basename "${script}")"
    echo "  -> Copying scripts/${fname}"
    ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "cat > /usrdata/simpleadmin/scripts/${fname}" < "${script}"
done

# Stream www assets
echo "  -> Copying www/index.html & login.html"
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "cat > /usrdata/simpleadmin/www/index.html" < "${SCRIPT_DIR}/www/index.html"
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "cat > /usrdata/simpleadmin/www/login.html" < "${SCRIPT_DIR}/www/login.html"

# Stream cgi-bin
for cgi in "${SCRIPT_DIR}"/www/cgi-bin/*; do
    cname="$(basename "${cgi}")"
    echo "  -> Copying www/cgi-bin/${cname}"
    ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "cat > /usrdata/simpleadmin/www/cgi-bin/${cname}" < "${cgi}"
done

# Set executable permissions and configure service state
echo "[4/5] Applying executable permissions and setting up autostart..."
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "
    chmod +x /usrdata/simpleadmin/scripts/*
    chmod +x /usrdata/simpleadmin/www/cgi-bin/*
    
    # Check if curl is present or symlink
    if [ ! -f /usrdata/simpleadmin/bin/curl ] && command -v curl >/dev/null; then
        ln -sf \$(which curl) /usrdata/simpleadmin/bin/curl
    fi

    # Symlink /data/simpleadmin to /usrdata/simpleadmin if not already present
    if [ ! -d /data/simpleadmin/scripts ]; then
        ln -sf /usrdata/simpleadmin/scripts /data/simpleadmin/scripts 2>/dev/null || true
    fi
    if [ ! -d /data/simpleadmin/www ]; then
        ln -sf /usrdata/simpleadmin/www /data/simpleadmin/www 2>/dev/null || true
    fi

    # Configure services_state.json
    mkdir -p /data/simpleadmin
    STATE_FILE=\"/data/simpleadmin/services_state.json\"
    if [ ! -f \"\$STATE_FILE\" ]; then
        echo '{\"tailscale_enabled\": 0, \"adblock_enabled\": 1, \"telemetry_enabled\": ${ENABLE_TELEMETRY}, \"blocked_domains_count\": 45000}' > \"\$STATE_FILE\"
    else
        if grep -q '\"telemetry_enabled\"' \"\$STATE_FILE\"; then
            sed -i 's/\"telemetry_enabled\": *[0-9]*/\"telemetry_enabled\": ${ENABLE_TELEMETRY}/' \"\$STATE_FILE\" 2>/dev/null || true
        else
            sed -i 's/{/{\"telemetry_enabled\": ${ENABLE_TELEMETRY}, /' \"\$STATE_FILE\" 2>/dev/null || true
        fi
    fi

    # Configure telemetry.conf
    CONF_FILE=\"/data/simpleadmin/telemetry.conf\"
    if [ ! -f \"\$CONF_FILE\" ]; then
        cat << 'EOF_CONF' > \"\$CONF_FILE\"
TELEMETRY_ENABLED=${ENABLE_TELEMETRY}
TELEMETRY_BASE_URL=\"${TELEMETRY_URL}\"
TELEMETRY_TOKEN=\"${TELEMETRY_TOKEN}\"
EOF_CONF
    else
        sed -i 's/^TELEMETRY_ENABLED=.*/TELEMETRY_ENABLED=${ENABLE_TELEMETRY}/' \"\$CONF_FILE\" 2>/dev/null || true
        if [ -n \"${TELEMETRY_URL}\" ]; then
            sed -i 's|^TELEMETRY_BASE_URL=.*|TELEMETRY_BASE_URL=\"${TELEMETRY_URL}\"|' \"\$CONF_FILE\" 2>/dev/null || true
        fi
        if [ -n \"${TELEMETRY_TOKEN}\" ]; then
            sed -i 's|^TELEMETRY_TOKEN=.*|TELEMETRY_TOKEN=\"${TELEMETRY_TOKEN}\"|' \"\$CONF_FILE\" 2>/dev/null || true
        fi
    fi
"

# Restart daemons
echo "[5/5] Restarting SimpleAdmin services on modem..."
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "
    pkill -f 'get_dashboard_data.pl.*--daemon' 2>/dev/null || true
    pkill -f 'httpd.*8080' 2>/dev/null || true
    pkill -f 'simpleadmin_daemon.sh' 2>/dev/null || true
    pkill -f 'telemetry_pusher.sh' 2>/dev/null || true
    
    # Launch supervisor daemon in background
    nohup /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh >/tmp/simpleadmin_daemon.log 2>&1 &
    sleep 2
    ps -ef | grep -E 'httpd|get_dashboard_data' | grep -v grep
"

echo "=== Deployment Complete! ==="
echo "Access the dashboard at: http://${MODEM_IP}:8080/login.html"
echo "Default password: admin"
