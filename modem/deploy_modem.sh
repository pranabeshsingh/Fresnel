#!/bin/bash
set -e

# ==============================================================================
# Fresnel 5G Suite - Automated Deployer
# Deploys Fresnel web GUI, telemetry daemon, and cellular tools over SSH
# ==============================================================================

MODEM_IP="${1:-172.16.10.1}"
SSH_USER="${2:-root}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Deploying Fresnel 5G Suite to ${SSH_USER}@${MODEM_IP} ==="

SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Test SSH connectivity
echo "[1/5] Checking SSH connection..."
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "uname -a" || {
    echo "ERROR: Unable to connect to ${SSH_USER}@${MODEM_IP} via SSH."
    echo "Usage: ./deploy_modem.sh [modem_ip] [ssh_user]"
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

# Set executable permissions
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
"

# Restart daemons
echo "[5/5] Restarting SimpleAdmin services on modem..."
ssh ${SSH_OPTS} "${SSH_USER}@${MODEM_IP}" "
    pkill -f 'get_dashboard_data.pl.*--daemon' 2>/dev/null || true
    pkill -f 'httpd.*8080' 2>/dev/null || true
    pkill -f 'simpleadmin_daemon.sh' 2>/dev/null || true
    
    # Launch supervisor daemon in background
    nohup /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh >/tmp/simpleadmin_daemon.log 2>&1 &
    sleep 2
    ps -ef | grep -E 'httpd|get_dashboard_data' | grep -v grep
"

echo "=== Deployment Complete! ==="
echo "Access the dashboard at: http://${MODEM_IP}:8080/login.html"
echo "Default password: admin"
