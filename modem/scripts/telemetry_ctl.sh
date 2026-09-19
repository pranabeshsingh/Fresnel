#!/bin/sh
# ==============================================================================
# Fresnel 5G Suite - Remote Telemetry Control Utility
# Manages state, configuration, and background daemons for VPS telemetry
# Usage: /usrdata/simpleadmin/scripts/telemetry_ctl.sh [status|enable|disable|config|uninstall]
# ==============================================================================

STATE_FILE="${STATE_FILE:-/data/simpleadmin/services_state.json}"
CONF_FILE="${CONF_FILE:-/data/simpleadmin/telemetry.conf}"
PUSHER_BIN="${PUSHER_BIN:-/usrdata/simpleadmin/scripts/telemetry_pusher.sh}"

mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
mkdir -p "$(dirname "$CONF_FILE")" 2>/dev/null || true

sed_inplace() {
    _EXPR="$1"
    _TARGET="$2"
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$_EXPR" "$_TARGET" 2>/dev/null || true
    else
        sed -i '' "$_EXPR" "$_TARGET" 2>/dev/null || sed -i "$_EXPR" "$_TARGET" 2>/dev/null || true
    fi
}

get_state() {
    if [ -f "$STATE_FILE" ]; then
        EN=$(grep -o '"telemetry_enabled": *[0-9]*' "$STATE_FILE" 2>/dev/null | tr -cd '0-9')
        [ -n "$EN" ] && echo "$EN" && return
    fi
    echo "0"
}

set_state() {
    VAL="$1"
    if [ ! -f "$STATE_FILE" ]; then
        echo "{\"tailscale_enabled\": 0, \"adblock_enabled\": 1, \"telemetry_enabled\": $VAL, \"blocked_domains_count\": 45000}" > "$STATE_FILE"
    else
        if grep -q '"telemetry_enabled"' "$STATE_FILE"; then
            sed_inplace "s/\"telemetry_enabled\": *[0-9]*/\"telemetry_enabled\": $VAL/" "$STATE_FILE"
        else
            sed_inplace "s/{/{\"telemetry_enabled\": $VAL, /" "$STATE_FILE"
        fi
    fi
    sync 2>/dev/null || true
}

get_conf_val() {
    KEY="$1"
    if [ -f "$CONF_FILE" ]; then
        grep -E "^${KEY}=" "$CONF_FILE" 2>/dev/null | head -n 1 | cut -d'=' -f2- | tr -d '"' | tr -d "'"
    fi
}

cmd_status() {
    EN=$(get_state)
    URL=$(get_conf_val "TELEMETRY_BASE_URL")
    TOK=$(get_conf_val "TELEMETRY_TOKEN")
    
    echo "=== Fresnel Remote Telemetry Status ==="
    if [ "$EN" = "1" ]; then
        echo "Telemetry: ENABLED"
    else
        echo "Telemetry: DISABLED"
    fi
    
    PID=$(pgrep -f "telemetry_pusher.sh" 2>/dev/null | head -n 1)
    if [ -n "$PID" ]; then
        echo "Process:   RUNNING (PID $PID)"
    else
        echo "Process:   STOPPED"
    fi
    
    if [ -n "$URL" ]; then
        echo "Endpoint:  $URL"
    else
        echo "Endpoint:  (not configured)"
    fi
    
    if [ -n "$TOK" ]; then
        MASKED="$(echo "$TOK" | cut -c 1-4)...$(echo "$TOK" | awk '{print substr($0, length($0)-3)}')"
        echo "Auth:      Token configured ($MASKED)"
    else
        echo "Auth:      (no token set)"
    fi
}

cmd_enable() {
    set_state 1
    if [ -f "$CONF_FILE" ]; then
        if grep -q '^TELEMETRY_ENABLED=' "$CONF_FILE"; then
            sed_inplace 's/^TELEMETRY_ENABLED=.*/TELEMETRY_ENABLED=1/' "$CONF_FILE"
        else
            echo "TELEMETRY_ENABLED=1" >> "$CONF_FILE"
        fi
    else
        echo "TELEMETRY_ENABLED=1" > "$CONF_FILE"
    fi
    
    URL=$(get_conf_val "TELEMETRY_BASE_URL")
    if [ -n "$URL" ] && [ "$URL" != "https://modem.example.com" ] && [ "$URL" != "https://your-vps-domain.com" ]; then
        if [ -x "$PUSHER_BIN" ] && ! pgrep -f "telemetry_pusher.sh" >/dev/null; then
            "$PUSHER_BIN" >/tmp/telemetry_pusher.log 2>&1 &
            echo "Remote telemetry enabled and pusher started."
            return 0
        fi
    fi
    echo "Remote telemetry enabled (pusher idle - configure VPS endpoint to start streaming)."
}

cmd_disable() {
    set_state 0
    if [ -f "$CONF_FILE" ]; then
        sed_inplace 's/^TELEMETRY_ENABLED=.*/TELEMETRY_ENABLED=0/' "$CONF_FILE"
    fi
    pkill -f "telemetry_pusher.sh" 2>/dev/null || true
    echo "Remote telemetry disabled and background pusher stopped."
}

cmd_config() {
    URL="$1"
    TOK="$2"
    if [ -z "$URL" ]; then
        echo "Usage: $0 config <base_url> [token]"
        exit 1
    fi
    
    CUR_EN=$(get_state)
    cat << EOF > "$CONF_FILE"
TELEMETRY_ENABLED=$CUR_EN
TELEMETRY_BASE_URL="$URL"
TELEMETRY_TOKEN="$TOK"
EOF
    echo "Telemetry configuration updated."
    
    if [ "$CUR_EN" = "1" ]; then
        pkill -f "telemetry_pusher.sh" 2>/dev/null || true
        if [ -x "$PUSHER_BIN" ]; then
            "$PUSHER_BIN" >/tmp/telemetry_pusher.log 2>&1 &
            echo "Restarted telemetry pusher with new configuration."
        fi
    fi
}

cmd_uninstall() {
    cmd_disable
    rm -f "$CONF_FILE" 2>/dev/null || true
    rm -f "$PUSHER_BIN" 2>/dev/null || true
    echo "Remote telemetry configuration and pusher scripts removed."
}

case "$1" in
    status)
        cmd_status
        ;;
    enable)
        cmd_enable
        ;;
    disable)
        cmd_disable
        ;;
    config)
        shift
        cmd_config "$@"
        ;;
    uninstall)
        cmd_uninstall
        ;;
    *)
        echo "Fresnel Remote Telemetry Control"
        echo "Usage: $0 {status|enable|disable|config <url> [token]|uninstall}"
        exit 1
        ;;
esac
